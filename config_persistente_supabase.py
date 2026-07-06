# =========================
# CONFIGURAÇÃO PERSISTENTE NO SUPABASE
# =========================
import os
import json
import socket
from urllib.parse import urlparse
from supabase import create_client

_supabase_client = None

# Variáveis para fallback local
CONFIG_FILE = "config.json"

def _load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def _save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _looks_configured_secret(value):
    return isinstance(value, str) and value.strip() and not value.strip().upper().startswith("COLE_")

def _looks_supabase_url(value):
    if not _looks_configured_secret(value):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)

def _can_resolve_url_hostname(value):
    if not _looks_supabase_url(value):
        return False
    hostname = urlparse(value.strip()).hostname
    if not hostname:
        return False
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except OSError:
        return False

def _choose_supabase_url(cfg):
    env_url = os.getenv("SUPABASE_URL")
    config_url = cfg.get("supabase_url")

    if _looks_supabase_url(env_url):
        if _can_resolve_url_hostname(env_url):
            return env_url.strip()
        if _looks_supabase_url(config_url) and _can_resolve_url_hostname(config_url):
            print("[DB] SUPABASE_URL do ambiente nao resolveu DNS; usando supabase_url do config.json.")
            return config_url.strip()
        return env_url.strip()

    if _looks_supabase_url(config_url):
        return config_url.strip()

    return env_url or config_url

def _choose_secret(cfg, env_name, config_key):
    env_value = os.getenv(env_name)
    if _looks_configured_secret(env_value):
        return env_value.strip()

    config_value = cfg.get(config_key)
    if _looks_configured_secret(config_value):
        return config_value.strip()

    return env_value or config_value

def _init_supabase():
    global _supabase_client
    if _supabase_client:
        return _supabase_client

    cfg = _load_config()
    url = _choose_supabase_url(cfg)
    key = _choose_secret(cfg, "SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key")
    
    if url and key:
        try:
            _supabase_client = create_client(url, key)
        except Exception as e:
            print(f"[DB] Erro ao conectar Supabase: {e}")
    
    return _supabase_client

def require_supabase():
    return _init_supabase()


# Adicione estas funções no bot.py (substitua as funções existentes)

# Tabela no Supabase:
# CREATE TABLE bot_config (
#     id SERIAL PRIMARY KEY,
#     key TEXT UNIQUE NOT NULL,
#     value JSONB NOT NULL,
#     updated_at TIMESTAMP DEFAULT NOW()
# );

def get_config_from_db(key: str, default=None):
    """Busca configuração do Supabase"""
    sb = require_supabase()
    if not sb:
        # Fallback para arquivo local
        cfg = _load_config()
        return cfg.get(key, default)
    
    try:
        result = sb.table("bot_config").select("value").eq("key", key).maybe_single().execute()
        if result.data:
            return result.data["value"]
    except Exception as e:
        print(f"[DB] Erro ao buscar config {key}: {e}")
    
    cfg = _load_config()
    return cfg.get(key, default)

def set_config_in_db(key: str, value):
    """Salva configuração no Supabase"""
    sb = require_supabase()
    if not sb:
        cfg = _load_config()
        cfg[key] = value
        _save_config(cfg)
        return
    
    try:
        sb.table("bot_config").upsert({
            "key": key,
            "value": value,
            "updated_at": "now()"
        }).execute()
    except Exception as e:
        print(f"[DB] Erro ao salvar config {key}: {e}")
    
    cfg = _load_config()
    cfg[key] = value
    _save_config(cfg)

# ============ SUBSTITUIR ESTAS FUNÇÕES ============

def get_supreme_role_id():
    return get_config_from_db("supreme_role_id")

def set_supreme_role_id(role_id):
    set_config_in_db("supreme_role_id", role_id)

def get_role_hierarchy():
    return get_config_from_db("role_hierarchy", {})

def set_role_hierarchy(role_id, level):
    hierarchy = get_role_hierarchy()
    hierarchy[str(role_id)] = level
    set_config_in_db("role_hierarchy", hierarchy)

def remove_role_hierarchy(role_id):
    hierarchy = get_role_hierarchy()
    if str(role_id) in hierarchy:
        del hierarchy[str(role_id)]
        set_config_in_db("role_hierarchy", hierarchy)

def get_command_permissions():
    return get_config_from_db("command_permissions", {})

def set_command_permission(role_id, command, allowed):
    perms = get_command_permissions()
    if str(role_id) not in perms:
        perms[str(role_id)] = {}
    perms[str(role_id)][command] = allowed
    set_config_in_db("command_permissions", perms)

def get_auto_delete_config():
    return get_config_from_db("auto_delete", {"enabled": False, "delay_seconds": 30, "commands": {}})

def set_auto_delete_enabled(enabled: bool):
    cfg = get_auto_delete_config()
    cfg["enabled"] = enabled
    set_config_in_db("auto_delete", cfg)

def set_auto_delete_delay(seconds: int):
    cfg = get_auto_delete_config()
    cfg["delay_seconds"] = max(5, min(300, seconds))
    set_config_in_db("auto_delete", cfg)

def set_command_auto_delete(command_name: str, enabled: bool):
    cfg = get_auto_delete_config()
    if "commands" not in cfg:
        cfg["commands"] = {}
    cfg["commands"][command_name] = enabled
    set_config_in_db("auto_delete", cfg)

# Inicializa chaves padrão se não existirem
def init_config_keys():
    """Inicializa chaves padrão no banco"""
    sb = require_supabase()
    if not sb:
        return
    
    defaults = [
        ("supreme_role_id", None),
        ("role_hierarchy", {}),
        ("command_permissions", {}),
        ("auto_delete", {"enabled": False, "delay_seconds": 30, "commands": {}}),
    ]
    
    for key, default_value in defaults:
        existing = get_config_from_db(key)
        if existing is None:
            set_config_in_db(key, default_value)
            print(f"[DB] Config '{key}' inicializada com padrão")

