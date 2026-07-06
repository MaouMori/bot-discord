import json
import os
import hashlib
import threading
import random
import requests
import re
import io
import socket
import unicodedata
import asyncio
from collections import deque
from urllib.parse import urlparse
from supabase import create_client
from config_persistente_supabase import (
    get_config_from_db, set_config_in_db,
    get_supreme_role_id, set_supreme_role_id,
    get_role_hierarchy, set_role_hierarchy, remove_role_hierarchy,
    get_command_permissions, set_command_permission,
    get_auto_delete_config, set_auto_delete_enabled, set_auto_delete_delay, set_command_auto_delete,
    init_config_keys,
)

# Inicializa chaves de config no banco
init_config_keys()

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from datetime import datetime
import time


import discord
from discord.ext import commands, tasks

PROCESSED_COMMAND_MESSAGES = set()
PROCESSED_COMMAND_ORDER = deque(maxlen=500)


def acquire_distributed_command_lock(message):
    lock_key = f"command_lock:{message.id}"

    sb = globals().get("supabase") or globals().get("supabase_client")
    if not sb:
        return True

    try:
        sb.table("bot_config").insert({
            "key": lock_key,
            "value": {
                "command": str(message.content or "")[:120],
                "channel_id": str(getattr(message.channel, "id", "")),
                "author_id": str(getattr(message.author, "id", "")),
            },
            "updated_at": "now()",
        }).execute()
        return True
    except Exception as e:
        text = str(e).lower()
        if "duplicate" in text or "23505" in text or "conflict" in text:
            return False
        print("[COMMAND LOCK] Nao foi possivel criar trava compartilhada:", e)
        return True


async def process_commands_once(message):
    if not message.content.startswith("!"):
        return

    message_id = message.id
    if message_id in PROCESSED_COMMAND_MESSAGES:
        return

    if not acquire_distributed_command_lock(message):
        return

    PROCESSED_COMMAND_MESSAGES.add(message_id)
    PROCESSED_COMMAND_ORDER.append(message_id)
    while len(PROCESSED_COMMAND_MESSAGES) > PROCESSED_COMMAND_ORDER.maxlen:
        oldest = PROCESSED_COMMAND_ORDER.popleft()
        PROCESSED_COMMAND_MESSAGES.discard(oldest)

    await bot.process_commands(message)


# =========================
# KEEP ALIVE (RENDER)
# =========================
def keep_alive():
    port = int(os.environ.get("PORT", 10000))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot online")

        def log_message(self, format, *args):
            return

    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


threading.Thread(target=keep_alive, daemon=True).start()


# =========================
# CONFIG
# =========================
CONFIG_FILE = "config.json"
STORAGE_FILE = "storage.json"

def stitch_memory_fallback(contexto: str) -> str:
    return (
        "⚠️ Hm... cérebro alienígena meio fritinho agora 😵\n"
        "Mas eu puxei isso da memória do 626:\n\n"
        f"{contexto}\n\n"
        "Hihi... viu? Stitch ainda brilha até sem nave. 👽✨"
    )


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


config = load_json(CONFIG_FILE)
storage = load_json(STORAGE_FILE)


def looks_configured_secret(value):
    return isinstance(value, str) and value.strip() and not value.strip().upper().startswith("COLE_")


def looks_supabase_url(value):
    if not looks_configured_secret(value):
        return False

    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def can_resolve_url_hostname(value):
    if not looks_supabase_url(value):
        return False

    hostname = urlparse(value.strip()).hostname
    if not hostname:
        return False

    try:
        socket.getaddrinfo(hostname, None)
        return True
    except OSError:
        return False


def choose_supabase_url():
    env_url = os.getenv("SUPABASE_URL")
    config_url = config.get("supabase_url")

    if looks_supabase_url(env_url):
        if can_resolve_url_hostname(env_url):
            return env_url.strip()

        if looks_supabase_url(config_url) and can_resolve_url_hostname(config_url):
            print("[SUPABASE] SUPABASE_URL do ambiente nao resolveu DNS; usando supabase_url do config.json.")
            return config_url.strip()

        return env_url.strip()

    if looks_supabase_url(config_url):
        return config_url.strip()

    return env_url or config_url


def choose_configured_secret(env_name, config_key):
    env_value = os.getenv(env_name)
    if looks_configured_secret(env_value):
        return env_value.strip()

    config_value = config.get(config_key)
    if looks_configured_secret(config_value):
        return config_value.strip()

    return env_value or config_value


SYNC_ERROR_STATE = {}


def log_sync_error(key, prefix, error, cooldown_seconds=300):
    now = time.time()
    message = str(error)
    state = SYNC_ERROR_STATE.get(key, {})

    if state.get("message") != message or now - state.get("last_print", 0) >= cooldown_seconds:
        print(f"{prefix} {message}")
        SYNC_ERROR_STATE[key] = {"message": message, "last_print": now}


KNOWLEDGE_FILE = "knowledge.json"


def load_knowledge():
    if not os.path.exists(KNOWLEDGE_FILE):
        return {}
    with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_knowledge(data):
    with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


knowledge = load_knowledge()
print(">>> BOT ATUAL CARREGADO <<<")
print(">>> KNOWLEDGE EXISTE:", isinstance(knowledge, dict))
print(">>> ARQUIVO:", os.path.abspath(__file__))

SUPABASE_URL = choose_supabase_url()
SUPABASE_SERVICE_ROLE_KEY = choose_configured_secret("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key")

supabase = None

if looks_supabase_url(SUPABASE_URL) and looks_configured_secret(SUPABASE_SERVICE_ROLE_KEY):
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        print(">>> SUPABASE CONECTADO <<<")
    except Exception as e:
        print("ERRO SUPABASE:", e)
else:
    print(">>> SUPABASE NAO CONFIGURADO (faltam url ou key validas) <<<")

def normalize_text(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_key(text: str) -> str:
    return normalize_text(text)




def score_knowledge_match(question: str, key: str, data: dict) -> int:
    msg = normalize_text(question)

    aliases = [normalize_text(key)]
    aliases += [normalize_text(alias) for alias in data.get("aliases", [])]

    score = 0

    for alias in aliases:
        if not alias:
            continue

        if alias == msg:
            score += 100
        elif alias in msg:
            score += 25

            # bonus por alias com mais de uma palavra
            if " " in alias:
                score += 10

    category = normalize_text(data.get("category", ""))
    if category and category in msg:
        score += 5

    return score

def get_knowledge_context(question: str, limit: int = 5) -> str:
    if not question:
        return ""

    results = []

    for key, data in knowledge.items():
        if isinstance(data, str):
            data = {
                "aliases": [key],
                "category": "geral",
                "content": data
            }

        score = score_knowledge_match(question, key, data)

        if score > 0:
            results.append((score, key, data))

    if not results:
        return "Nenhum conhecimento específico encontrado."

    results.sort(key=lambda item: item[0], reverse=True)
    selected = results[:limit]

    lines = []
    for score, key, data in selected:
        category = data.get("category", "geral")
        content = data.get("content", "")
        lines.append(f"• {key} ({category}): {content}")

    return "\n".join(lines)

# =========================
# GARANTE ESTRUTURA
# =========================
storage.setdefault("pending_requests", {})
storage.setdefault("approved_members", [])

storage.setdefault("pending_role_requests", {})
storage.setdefault("approved_role_requests", [])

storage.setdefault("pending_member_requests", {})
storage.setdefault("notified_link_request_ids", [])
storage.setdefault("notified_pending_link_request_ids", [])

storage.setdefault("open_tickets", {})
storage.setdefault("ticket_ai_disabled", {})
storage.setdefault("ticket_assumed_by", {})

# =========================
# HIERARQUIA E PERMISSÕES
# =========================
# Usar config.json para persistir entre deploys
config.setdefault("role_hierarchy", {})  # {role_id: nivel}
config.setdefault("command_permissions", {})  # {comando: {role_id: True/False}}
config.setdefault("supreme_role_id", None)  # Cargo supremo (acesso total)

def get_role_hierarchy():
    return config.get("role_hierarchy", {})





def is_supreme_member(member):
    """Verifica se o membro tem o cargo supremo"""
    supreme_id = get_supreme_role_id()
    if not supreme_id:
        return False
    return any(role.id == supreme_id for role in member.roles)

def can_use_command(member, command_name):
    """Verifica se o membro pode usar um comando específico"""
    # Cargo supremo pode tudo
    if is_supreme_member(member):
        return True
    
    # Verifica permissões específicas do comando
    perms = get_command_permissions()
    command_perms = perms.get(command_name, {})
    
    # Se não há permissões definidas, verifica hierarquia mínima (nível 1)
    if not command_perms:
        return get_member_highest_level(member) >= 1
    
    # Verifica se algum cargo do membro tem permissão explícita
    for role in member.roles:
        if command_perms.get(str(role.id), False):
            return True
    
    return False

def set_command_permission(role_id, command_name, allowed):
    """Define permissão de um cargo para usar um comando"""
    perms = get_command_permissions()
    
    if command_name not in perms:
        perms[command_name] = {}
    
    perms[command_name][str(role_id)] = allowed
    set_config_in_db("command_permissions", perms)

def require_permission(command_name=None):
    """Decorador para verificar permissões em comandos"""
    def decorator(func):
        async def wrapper(ctx, *args, **kwargs):
            cmd = command_name or func.__name__
            
            if not isinstance(ctx.author, discord.Member):
                await ctx.send("❌ Não foi possível verificar suas permissões.")
                return
            
            if not can_use_command(ctx.author, cmd):
                await ctx.send("❌ Você não tem permissão para usar este comando.")
                return
            
            return await func(ctx, *args, **kwargs)
        
        # Preserva o nome e docstring da função original
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator

def require_hierarchy_level(min_level):
    """Decorador para exigir nível hierárquico mínimo"""
    def decorator(func):
        async def wrapper(ctx, *args, **kwargs):
            if not isinstance(ctx.author, discord.Member):
                await ctx.send("❌ Não foi possível verificar suas permissões.")
                return
            
            # Cargo supremo sempre passa
            if is_supreme_member(ctx.author):
                return await func(ctx, *args, **kwargs)
            
            user_level = get_member_highest_level(ctx.author)
            
            if user_level < min_level:
                await ctx.send(f"❌ Você precisa ser nível {min_level}+ para usar este comando.")
                return
            
            return await func(ctx, *args, **kwargs)
        
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator

def require_supreme():
    """Decorador para comandos exclusivos do cargo supremo"""
    def decorator(func):
        async def wrapper(ctx, *args, **kwargs):
            if not isinstance(ctx.author, discord.Member):
                await ctx.send("❌ Não foi possível verificar suas permissões.")
                return
            
            if not is_supreme_member(ctx.author):
                await ctx.send("❌ Apenas o cargo supremo pode usar este comando.")
                return
            
            return await func(ctx, *args, **kwargs)
        
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator

def persist_storage():
    save_json(STORAGE_FILE, storage)


# =========================
# AUTO-DELETE SYSTEM
# =========================
# Usar config.json para persistir entre deploys
config.setdefault("auto_delete", {
    "enabled": False,
    "delay_seconds": 30,
    "commands": {}
})

def get_member_highest_level(member):
    hierarchy = get_role_hierarchy()
    if not hierarchy:
        return 0
    
    max_level = 0
    for role in member.roles:
        role_level = hierarchy.get(str(role.id))
        if role_level is not None and role_level > max_level:
            max_level = role_level
    
    return max_level


def list_auto_delete_commands():
    """Retorna lista de comandos com auto-delete configurado"""
    cfg = get_auto_delete_config()
    return cfg.get("commands", {})

def get_command_auto_delete(command_name: str):
    """Retorna se um comando específico tem auto-delete ativado"""
    cfg = get_auto_delete_config()
    return bool(cfg.get("commands", {}).get(str(command_name or "").lower(), False))


def should_auto_delete_for_command(command_name: str):
    """Decide se deve auto-deletar para um comando"""
    cfg = get_auto_delete_config()
    if not cfg.get("enabled", False):
        return False

    cmd = str(command_name or "").strip().lower()
    if not cmd:
        return False

    return get_command_auto_delete(cmd)

async def auto_delete_message(message, has_view=False, command_name=None):
    """Apaga mensagem automaticamente conforme configuração de comando"""
    cmd = str(command_name or "").strip().lower()
    if not should_auto_delete_for_command(cmd):
        return

    ad_config = get_auto_delete_config()
    await asyncio.sleep(ad_config.get("delay_seconds", 30))
    try:
        await message.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        pass

async def send_auto_delete(ctx, content=None, *, embed=None, view=None, command_name=None, **kwargs):
    """Envia mensagem com auto-delete inteligente"""
    cmd = str(
        command_name
        or (
            getattr(getattr(ctx, "command", None), "name", "")
            if getattr(ctx, "command", None)
            else ""
        )
    ).strip().lower()

    message = await ctx.send(content=content, embed=embed, view=view, **kwargs)
    asyncio.create_task(auto_delete_message(message, has_view=False, command_name=cmd))
    return message

async def reply_auto_delete(ctx, content=None, *, embed=None, view=None, command_name=None, **kwargs):
    """Responde com auto-delete inteligente"""
    cmd = str(
        command_name
        or (
            getattr(getattr(ctx, "command", None), "name", "")
            if getattr(ctx, "command", None)
            else ""
        )
    ).strip().lower()

    message = await ctx.reply(content=content, embed=embed, view=view, **kwargs)
    asyncio.create_task(auto_delete_message(message, has_view=False, command_name=cmd))
    return message


# =========================
# BOT
# =========================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")


@bot.before_invoke
async def _autodelete_before_invoke(ctx):
    cmd_name = str(getattr(getattr(ctx, "command", None), "name", "")).strip().lower()
    enabled = should_auto_delete_for_command(cmd_name)

    ctx._autodelete_enabled = enabled
    ctx._autodelete_messages = []
    ctx._autodelete_original_send = None
    ctx._autodelete_original_reply = None

    if not enabled:
        return

    original_send = ctx.send
    original_reply = ctx.reply
    ctx._autodelete_original_send = original_send
    ctx._autodelete_original_reply = original_reply

    async def wrapped_send(*args, **kwargs):
        msg = await original_send(*args, **kwargs)
        ctx._autodelete_messages.append(msg)
        return msg

    async def wrapped_reply(*args, **kwargs):
        msg = await original_reply(*args, **kwargs)
        ctx._autodelete_messages.append(msg)
        return msg

    ctx.send = wrapped_send
    ctx.reply = wrapped_reply


@bot.after_invoke
async def _autodelete_after_invoke(ctx):
    original_send = getattr(ctx, "_autodelete_original_send", None)
    original_reply = getattr(ctx, "_autodelete_original_reply", None)
    if original_send:
        ctx.send = original_send
    if original_reply:
        ctx.reply = original_reply

    if not getattr(ctx, "_autodelete_enabled", False):
        return

    cmd_name = str(getattr(getattr(ctx, "command", None), "name", "")).strip().lower()

    seen = set()
    messages = []
    for msg in getattr(ctx, "_autodelete_messages", []):
        msg_id = getattr(msg, "id", None)
        if msg_id and msg_id in seen:
            continue
        if msg_id:
            seen.add(msg_id)
        messages.append(msg)

    for msg in messages:
        asyncio.create_task(auto_delete_message(msg, command_name=cmd_name))

    if getattr(ctx, "message", None):
        asyncio.create_task(auto_delete_message(ctx.message, command_name=cmd_name))

# =========================
# HELPERS
# =========================

SUPABASE_URL = choose_supabase_url()
SUPABASE_SERVICE_ROLE_KEY = choose_configured_secret("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key")
SUPABASE_ADMIN_UUID = choose_configured_secret("SUPABASE_ADMIN_UUID", "supabase_admin_uuid")

def get_ticket_ai_disabled():
    return storage["ticket_ai_disabled"]


def get_ticket_assumed_by():
    return storage["ticket_assumed_by"]


def is_ticket_channel(channel) -> bool:
    if channel is None:
        return False

    open_tickets = get_open_tickets()
    return channel.id in open_tickets.values()


def get_ticket_owner_id(channel_id: int):
    for user_id, ticket_channel_id in get_open_tickets().items():
        if ticket_channel_id == channel_id:
            return int(user_id)
    return None


def get_embed_color():
    return discord.Color(config.get("embed_color", 5793266))


def clean_bind_token(value):
    return str(value or "").strip().strip("\"'")


def get_bind_ids(value):
    matches = re.findall(r"[A-Za-z0-9_-]+", str(value or ""))
    ids = []
    seen = set()

    for match in matches:
        item = clean_bind_token(match)
        if not item or item in seen:
            continue
        ids.append(item)
        seen.add(item)

    return ids


def build_dance_bind(key, prefix, dance, ids_text):
    key = clean_bind_token(key)
    prefix = clean_bind_token(prefix)
    dance = clean_bind_token(dance)
    ids = get_bind_ids(ids_text)

    if not key or not prefix or not dance or not ids:
        return None, "Preencha tecla, comando, dança e pelo menos 1 ID.", ids

    inner_command = "; ".join(f"{prefix} {dance} {player_id}" for player_id in ids)
    return f'bind keyboard "{key}" "{inner_command}"', None, ids


async def send_dance_bind_result(target, key, prefix, dance, ids_text, *, ephemeral=False):
    bind, error, ids = build_dance_bind(key, prefix, dance, ids_text)

    if error:
        message = (
            f"❌ {error}\n"
            "Exemplo: `!binddanca F6 e3 dancar23 3234 3214`"
        )
        if isinstance(target, discord.Interaction):
            await target.response.send_message(message, ephemeral=ephemeral)
        else:
            await target.send(message)
        return

    embed = discord.Embed(
        title="Gerador de Bind de Dança",
        description=f"Bind pronta com **{len(ids)}** ID{'s' if len(ids) != 1 else ''}.",
        color=get_embed_color()
    )
    embed.add_field(name="Tecla", value=f"`{clean_bind_token(key)}`", inline=True)
    embed.add_field(name="Comando", value=f"`{clean_bind_token(prefix)}`", inline=True)
    embed.add_field(name="Dança", value=f"`{clean_bind_token(dance)}`", inline=True)
    embed.set_footer(text='Formato: bind keyboard "tecla" "comando dança id; comando dança id"')

    file = None
    if len(bind) <= 930:
        embed.add_field(name="Resultado", value=f"```{bind}```", inline=False)
    else:
        embed.add_field(
            name="Resultado",
            value="A bind ficou grande demais para o embed, então enviei em arquivo `.txt`.",
            inline=False
        )
        file = discord.File(io.BytesIO(bind.encode("utf-8")), filename="bind-danca.txt")

    if isinstance(target, discord.Interaction):
        await target.response.send_message(embed=embed, file=file, ephemeral=ephemeral)
    else:
        await target.send(embed=embed, file=file)


def get_pending_requests():
    return storage["pending_requests"]


def get_approved_members():
    return storage["approved_members"]


def get_pending_role_requests():
    return storage["pending_role_requests"]


def get_approved_role_requests():
    return storage["approved_role_requests"]


def get_open_tickets():
    return storage["open_tickets"]


def has_approver_role(member):
    approver_role_ids = config.get("approver_role_ids")

    if approver_role_ids is None:
        single_role_id = config.get("approver_role_id")
        approver_role_ids = [single_role_id] if single_role_id else []

    return any(role.id in approver_role_ids for role in member.roles)


def has_ticket_staff_role(member):
    staff_role_id = config.get("ticket_staff_role_id")
    if not staff_role_id:
        return has_approver_role(member)
    return any(role.id == staff_role_id for role in member.roles)


def is_registered_member(member):
    default_role_id = config.get("default_role_id")
    requestable_role_ids = set(config.get("requestable_roles", {}).values())

    member_role_ids = {role.id for role in member.roles}

    if default_role_id and default_role_id in member_role_ids:
        return True

    if requestable_role_ids.intersection(member_role_ids):
        return True

    return False


async def fetch_guild_member(guild, user_id):
    member = guild.get_member(user_id)
    if member:
        return member

    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def should_receive_default_role(requested_role_id):
    default_role_id = config.get("default_role_id")
    if not default_role_id:
        return False

    roles_without_default = set(config.get("roles_without_default", []))
    return int(requested_role_id) not in roles_without_default


async def add_configured_role(member, role, reason):
    if not role:
        return "não encontrado"

    if role in member.roles:
        return "já possuía"

    try:
        await member.add_roles(role, reason=reason)
        return "adicionado"
    except discord.Forbidden:
        return "não adicionado (sem permissão/hierarquia)"
    except discord.HTTPException as e:
        return f"não adicionado ({e})"


def format_registration_nickname(character_name, character_id):
    name = str(character_name or "").strip()
    player_id = str(character_id or "").strip()

    if name and player_id:
        if name.endswith(f"| {player_id}") or name.endswith(f"|{player_id}"):
            return name[:32]

        suffix = f" | {player_id}"
        max_name_length = max(1, 32 - len(suffix))
        return f"{name[:max_name_length].strip()}{suffix}"[:32]

    return name[:32]


async def update_member_nickname(member, desired_nick, reason):
    desired_nick = str(desired_nick or "").strip()[:32]
    current_nick = member.nick or member.name

    if not desired_nick:
        return "não alterado (nome vazio)"

    if current_nick == desired_nick:
        return "já estava correto"

    try:
        await member.edit(nick=desired_nick, reason=reason)
        return f"alterado para `{desired_nick}`"
    except discord.Forbidden:
        return "não alterado (sem permissão/hierarquia)"
    except discord.HTTPException as e:
        return f"não alterado ({e})"


async def update_registration_nickname(guild, user_id, character_name, character_id, reason):
    member = await fetch_guild_member(guild, int(user_id))
    if not member:
        return "não alterado (membro não encontrado)"

    desired_nick = format_registration_nickname(character_name, character_id)
    return await update_member_nickname(member, desired_nick, reason)


def get_pending_member_requests():
    return storage["pending_member_requests"]


def cleanup_expired_member_requests(expiration_minutes=60):
    pending = get_pending_member_requests()
    now_ts = datetime.now().timestamp()
    expired = []
    
    for uid, data in pending.items():
        created = data.get("created_at", 0)
        if now_ts - created > expiration_minutes * 60:
            expired.append(uid)
    
    for uid in expired:
        pending.pop(uid, None)
    
    if expired:
        persist_storage()
    
    return len(expired)


def cleanup_expired_pending_requests(expiration_minutes=30):
    pending = get_pending_requests()
    now_ts = datetime.now().timestamp()
    expired_keys = []

    for user_id, request in pending.items():
        created_at = request.get("created_at")
        if created_at is None:
            continue

        age_seconds = now_ts - created_at
        if age_seconds > expiration_minutes * 60:
            expired_keys.append(user_id)

    for key in expired_keys:
        pending.pop(key, None)

    if expired_keys:
        persist_storage()

    return len(expired_keys)


def is_already_approved(user_id: int):
    approved_members = get_approved_members()
    return any(member_data.get("user_id") == user_id for member_data in approved_members)


async def ask_stitch_ai(question: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {config.get('openrouter_api_key')}",
        "Content-Type": "application/json"
    }

    context = get_knowledge_context(question)

    system_prompt = (
        "Você é o Stitch, Experimento 626, assistente oficial da Iconics. "
        "Fale em português. "
        "Seja engraçado, brincalhão, carismático, levemente caótico e um pouco debochado. "
        "Use humor e sonoplastias leves como 'hmm', 'grrr', 'bleh', 'hihi' quando combinar. "
        "Mas nunca deixe de responder corretamente. "
        "Primeiro responda de forma útil e clara. "
        "Depois, se fizer sentido, adicione uma piada curta ou comentário no estilo Stitch. "
        "Você deve usar como base principal o conhecimento abaixo sobre a Iconics e seus membros. "
        "Use os aliases apenas para reconhecer o assunto. Não mencione aliases na resposta, a menos que o usuário peça. "
        "Se não souber algo, diga claramente que ainda não aprendeu aquilo e que a staff pode ensinar.\n\n"
        f"Conhecimento atual:\n{context}"
    )

    # ordem de tentativa: preset Stitch -> modelos free de backup
    models_to_try = [
        "@preset/stitch",
        "mistralai/mistral-7b-instruct:free",
        "meta-llama/llama-3-8b-instruct:free",
    ]

    last_error = None

    for model_name in models_to_try:
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": question
                }
            ]
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=45)
            data = response.json()

            print("MODEL:", model_name)
            print("STATUS TICKET IA:", response.status_code)
            print("RESPOSTA TICKET IA:", data)

            if response.status_code == 200 and "choices" in data:
                return data["choices"][0]["message"]["content"]

            last_error = data

        except Exception as e:
            print("ERRO NO MODELO:", model_name, e)
            last_error = str(e)

    # fallback local com memória
    if context and "Nenhum conhecimento específico encontrado." not in context:
        return (
            "⚠️ Hm... meu cérebro alienígena ficou sem energia de API agora 😵\n"
            "Mas eu ainda lembro disso aqui:\n\n"
            f"{context}\n\n"
            "Hihi... até sem foguete eu ainda aterrissei bonito. 👽"
        )

    return (
        "⚠️ Grrr... meus neurônios espaciais travaram e eu também não achei nada útil na memória agora.\n"
        "Tenta de novo mais tarde ou ensina isso pra mim com a staff. 👽🔧"
    )

async def send_log_embed(guild, title, description, color=None):
    if guild is None:
        return

    log_channel_id = config.get("log_channel_id")
    if not log_channel_id:
        return

    log_channel = guild.get_channel(log_channel_id)
    if log_channel is None:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color or get_embed_color(),
        timestamp=datetime.now()
    )

    try:
        await log_channel.send(embed=embed)
    except discord.HTTPException:
        pass

    # Espelha logs no site (tabela public.discord_logs), se existir.
    sb = require_supabase()
    if not sb:
        return

    try:
        sb.table("discord_logs").insert(
            {
                "guild_id": str(guild.id),
                "channel_id": str(log_channel_id),
                "event_title": str(title or "")[:200],
                "event_description": str(description or "")[:4000],
                "level": "info",
                "created_at": datetime.utcnow().isoformat(),
            }
        ).execute()
    except Exception:
        # Não quebra o bot se a tabela não existir ainda.
        pass


# =========================
# REGISTRO
# =========================
class RejectModal(discord.ui.Modal, title="Motivo da recusa"):
    motivo = discord.ui.TextInput(
        label="Motivo",
        style=discord.TextStyle.paragraph
    )

    def __init__(self, user_id):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction):
        pending = get_pending_requests()
        data = pending.get(str(self.user_id))

        if not data:
            await interaction.response.send_message("Já processado.", ephemeral=True)
            return

        member = interaction.guild.get_member(self.user_id) if interaction.guild else None

        pending.pop(str(self.user_id), None)
        persist_storage()

        if member:
            try:
                await member.send(f"Seu registro foi recusado.\nMotivo: {self.motivo}")
            except discord.HTTPException:
                pass
        
        if interaction.guild:
            await send_log_embed(
                interaction.guild,
                "❌ Registro recusado",
                (
                    f"**Membro:** <@{self.user_id}>\n"
                    f"**Motivo:** {self.motivo}\n"
                    f"**Recusado por:** {interaction.user.mention}"
                ),
                discord.Color.red()
            )

        await interaction.response.edit_message(
            content=f"Recusado: {self.motivo}",
            embed=None,
            view=None
        )


class RejectButton(discord.ui.Button):
    def __init__(self, target_user_id):
        super().__init__(
            label="Recusar",
            style=discord.ButtonStyle.danger,
            custom_id=f"reject_request_{target_user_id}"
        )
        self.target_user_id = target_user_id

    async def callback(self, interaction):
        await interaction.response.send_modal(RejectModal(self.target_user_id))

class ApproveButton(discord.ui.Button):
    def __init__(self, target_user_id):
        super().__init__(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            custom_id=f"approve_request_{target_user_id}"
        )
        self.target_user_id = target_user_id

    async def callback(self, interaction):
        if not has_approver_role(interaction.user):
            await interaction.response.send_message(
                "Sem permissão.",
                ephemeral=True
            )
            return

        pending = get_pending_requests()
        data = pending.get(str(self.target_user_id))

        if not data:
            await interaction.response.send_message(
                "Pedido não encontrado.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        member = await fetch_guild_member(guild, self.target_user_id)

        if not member:
            await interaction.response.send_message(
                "Membro não encontrado.",
                ephemeral=True
            )
            return

        role = guild.get_role(data["requested_role_id"])

        if not role:
            await interaction.response.send_message(
                "Cargo não encontrado.",
                ephemeral=True
            )
            return

        approval_reason = f"Registro aprovado por {interaction.user}"
        desired_nickname = format_registration_nickname(
            data.get("character_name"),
            data.get("character_id")
        )
        nickname_status = await update_registration_nickname(
            guild,
            self.target_user_id,
            data.get("character_name"),
            data.get("character_id"),
            approval_reason
        )

        requested_role_status = await add_configured_role(member, role, approval_reason)

        if requested_role_status.startswith("não adicionado"):
            await interaction.response.send_message(
                (
                    "Não consegui adicionar o cargo solicitado. Verifique se o cargo "
                    "do bot está acima desse cargo e se ele tem permissão de gerenciar cargos."
                ),
                ephemeral=True
            )
            return

        default_role_status = "não configurado"
        default_role = None
        if should_receive_default_role(data["requested_role_id"]):
            default_role = guild.get_role(int(config.get("default_role_id")))
            default_role_status = await add_configured_role(member, default_role, approval_reason)
        elif config.get("default_role_id"):
            default_role_status = "ignorado para esse cargo"

        approved_members = get_approved_members()
        approved_members.append({
            "user_id": member.id,
            "discord_name": str(member),
            "character_name": data["character_name"],
            "character_id": data["character_id"],
            "recruiter_name": data["recruiter_name"],
            "requested_role_name": data["requested_role_name"],
            "approved_by": str(interaction.user),
            "approved_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        })

        pending.pop(str(self.target_user_id), None)
        persist_storage()
        
        await send_log_embed(
            guild,
            "✅ Registro aprovado",
            (
                f"**Membro:** {member.mention}\n"
                f"**Nome RP:** {data['character_name']}\n"
                f"**ID:** {data['character_id']}\n"
                f"**Cargo solicitado:** {role.name} ({requested_role_status})\n"
                f"**Cargo padrão:** "
                f"{default_role.name if default_role else 'não aplicado'} ({default_role_status})\n"
                f"**Nickname desejado:** `{desired_nickname}`\n"
                f"**Nickname:** {nickname_status}\n"
                f"**Aprovado por:** {interaction.user.mention}"
            ),
            discord.Color.green()
        )

        await interaction.message.edit(
            content=(
                f"Aprovado por {interaction.user.mention}\n"
                f"Nickname desejado: `{desired_nickname}`\n"
                f"Status do nickname: {nickname_status}"
            ),
            embed=None,
            view=None
        )

        try:
            await member.send(
                f"🎉 Seu registro foi aprovado!\n"
                f"Cargo solicitado: **{role.name}** ({requested_role_status})\n"
                f"Cargo padrão: **{default_role.name if default_role else 'não aplicado'}** ({default_role_status})\n"
                f"Nickname desejado: **{desired_nickname}**\n"
                f"Nickname: **{nickname_status}**"
            )
        except:
            pass

class ApprovalView(discord.ui.View):
    def __init__(self, target_user_id):
        super().__init__(timeout=None)
        self.add_item(ApproveButton(target_user_id))
        self.add_item(RejectButton(target_user_id))


class RegistrationModal(discord.ui.Modal, title="Registro de Membro"):
    character_name = discord.ui.TextInput(
        label="Nome do personagem",
        placeholder="Ex: Maou Devill",
        max_length=24,
        required=True
    )

    character_id = discord.ui.TextInput(
        label="ID do personagem",
        placeholder="Ex: 245",
        max_length=10,
        required=True
    )

    recruiter_name = discord.ui.TextInput(
        label="Quem recrutou você?",
        placeholder="Ex: Yuri",
        max_length=50,
        required=True
    )

    async def on_submit(self, interaction):
        try:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message(
                    "Não foi possível validar seu usuário no servidor.",
                    ephemeral=True
                )
                return

            pending = get_pending_requests()

            if str(interaction.user.id) in pending:
                await interaction.response.send_message(
                    "Você já tem uma solicitação de registro pendente.",
                    ephemeral=True
                )
                return

            if is_registered_member(interaction.user):
                await interaction.response.send_message(
                    "Você já possui um cargo de membro/registro no servidor.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                "Agora escolha o cargo que deseja solicitar:",
                ephemeral=True,
                view=RoleSelectionView(
                    user_id=interaction.user.id,
                    character_name=self.character_name.value.strip(),
                    character_id=self.character_id.value.strip(),
                    recruiter_name=self.recruiter_name.value.strip()
                )
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Erro ao abrir seleção de cargo: {e}",
                ephemeral=True
            )


class RoleSelect(discord.ui.Select):
    def __init__(self, user_id, character_name, character_id, recruiter_name):
        self.user_id = user_id
        self.character_name = character_name
        self.character_id = character_id
        self.registration_nickname = format_registration_nickname(character_name, character_id)
        self.recruiter_name = recruiter_name

        requestable_roles = config.get("requestable_roles", {})
        options = [
            discord.SelectOption(label=role_name, value=str(role_id))
            for role_name, role_id in requestable_roles.items()
        ]

        if not options:
            options = [discord.SelectOption(label="Nenhum cargo configurado", value="0")]

        super().__init__(
            placeholder="Selecione o cargo desejado",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Esse menu não é seu.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Não foi possível validar seu usuário no servidor.",
                ephemeral=True
            )
            return

        if is_already_approved(interaction.user.id):
            await interaction.response.send_message(
                "Você já foi aprovado anteriormente e não pode enviar outro registro.",
                ephemeral=True
            )
            return

        if is_registered_member(interaction.user):
            await interaction.response.send_message(
                "Você já possui um cargo de membro/registro no servidor.",
                ephemeral=True
            )
            return

        if not config.get("requestable_roles"):
            await interaction.response.send_message(
                "Nenhum cargo configurado no config.json.",
                ephemeral=True
            )
            return

        pending = get_pending_requests()
        request_key = str(interaction.user.id)

        if request_key in pending:
            await interaction.response.send_message(
                "Você já tem uma solicitação pendente.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Esse comando só funciona dentro do servidor.",
                ephemeral=True
            )
            return

        approval_channel = guild.get_channel(config.get("approval_channel_id"))
        if approval_channel is None:
            await interaction.response.send_message(
                "Canal de aprovação não encontrado. Verifique o config.json.",
                ephemeral=True
            )
            return

        selected_role_id = int(self.values[0])
        selected_role_name = next(
            (
                role_name
                for role_name, role_id in config["requestable_roles"].items()
                if role_id == selected_role_id
            ),
            "Desconhecido"
        )

        embed = discord.Embed(
            title="Nova solicitação de registro",
            color=get_embed_color()
        )
        embed.add_field(name="Usuário", value=interaction.user.mention, inline=False)
        embed.add_field(name="Nome", value=self.character_name, inline=True)
        embed.add_field(name="ID", value=self.character_id, inline=True)
        embed.add_field(name="Recrutador", value=self.recruiter_name, inline=False)
        embed.add_field(name="Cargo solicitado", value=selected_role_name, inline=False)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        try:
            await approval_channel.send(
                embed=embed,
                view=ApprovalView(target_user_id=interaction.user.id)
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Não foi possível enviar sua solicitação para aprovação: {e}",
                ephemeral=True
            )
            return

        pending[request_key] = {
            "user_id": interaction.user.id,
            "character_name": self.registration_nickname,
            "raw_character_name": self.character_name,
            "registration_nickname": self.registration_nickname,
            "character_id": self.character_id,
            "recruiter_name": self.recruiter_name,
            "requested_role_id": selected_role_id,
            "requested_role_name": selected_role_name,
            "created_at": datetime.now().timestamp()
        }
        persist_storage()

        await send_log_embed(
            guild,
            "📨 Nova solicitação de registro",
            (
                f"**Usuário:** {interaction.user.mention}\n"
                f"**Nome RP:** {self.character_name}\n"
                f"**ID:** {self.character_id}\n"
                f"**Recrutador:** {self.recruiter_name}\n"
                f"**Cargo solicitado:** {selected_role_name}"
            ),
            discord.Color.blurple()
        )

        await interaction.response.edit_message(
            content="Sua solicitação foi enviada para aprovação.",
            view=None,
            delete_after=15
        )


class RoleSelectionView(discord.ui.View):
    def __init__(self, user_id, character_name, character_id, recruiter_name):
        super().__init__(timeout=300)
        self.add_item(
            RoleSelect(
                user_id=user_id,
                character_name=character_name,
                character_id=character_id,
                recruiter_name=recruiter_name
            )
        )

class SiteFormButton(discord.ui.Button):
    def __init__(self):
        form_url = config.get("iconics_form_url", "").strip()
        
        # Só cria o botão se tiver URL válida
        if form_url and form_url.startswith(('http://', 'https://')):
            super().__init__(
                label="Formulário no Site",
                style=discord.ButtonStyle.link,
                url=form_url
            )
        else:
            # Se não tiver URL, não cria o botão (ou cria desabilitado)
            super().__init__(
                label="Formulário no Site",
                style=discord.ButtonStyle.link,
                url="https://iconics-jade.vercel.app",
                disabled=True
            )

class RegisterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Registrar",
            style=discord.ButtonStyle.primary,
            custom_id="persistent_register_button"
        )

    async def callback(self, interaction):
        try:
            await interaction.response.send_modal(RegistrationModal())
        except discord.NotFound:
            return

        cleanup_expired_pending_requests()

class RegisterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RegisterButton())

        form_url = config.get("iconics_form_url", "").strip()
        if form_url:
            self.add_item(SiteFormButton())


@bot.command(name="painelregistro")
async def painel_registro(ctx):
    embed = discord.Embed(
        title="Registro de Membros",
        description=(
            "Escolha uma das opções abaixo:\n\n"
            "🟣 **Registrar** → faz o registro direto no Discord\n"
            "🌐 **Formulário no Site** → abre o formulário oficial da Iconics\n\n"
            "Use a opção que a staff orientar."
        ),
        color=get_embed_color()
    )

    await ctx.send(embed=embed, view=RegisterView())


class RoleRequestModal(discord.ui.Modal, title="Solicitação de Cargo"):
    motivo = discord.ui.TextInput(
        label="Por que você merece esse cargo?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction):
        try:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message(
                    "Não foi possível validar seu usuário no servidor.",
                    ephemeral=True
                )
                return

            if str(interaction.user.id) in get_pending_role_requests():
                await interaction.response.send_message(
                    "Você já tem um pedido de cargo pendente.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                "Escolha o cargo desejado:",
                view=RoleRequestView(interaction.user.id, str(self.motivo.value).strip()),
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Erro ao abrir seleção de cargo: {e}",
                ephemeral=True
            )


class RoleRequestSelect(discord.ui.Select):
    def __init__(self, user_id, motivo):
        self.user_id = user_id
        self.motivo = motivo

        requestable_roles = config.get("member_requestable_roles", {})
        options = [
            discord.SelectOption(label=nome_cargo, value=str(role_id))
            for nome_cargo, role_id in requestable_roles.items()
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="Nenhum cargo configurado",
                    value="0"
                )
            ]

        super().__init__(
            placeholder="Selecione o cargo",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Esse menu não é seu.",
                ephemeral=True
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Não foi possível validar seu usuário no servidor.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Servidor não encontrado.",
                ephemeral=True
            )
            return

        pending = get_pending_role_requests()
        if str(interaction.user.id) in pending:
            await interaction.response.send_message(
                "Você já tem um pedido de cargo pendente.",
                ephemeral=True
            )
            return

        selected_value = self.values[0]
        if selected_value == "0":
            await interaction.response.send_message(
                "Nenhum cargo configurado no config.json.",
                ephemeral=True
            )
            return

        role_id = int(selected_value)
        role = guild.get_role(role_id)

        if role is None:
            await interaction.response.send_message(
                "Esse cargo não foi encontrado no servidor.",
                ephemeral=True
            )
            return

        if role in interaction.user.roles:
            await interaction.response.send_message(
                "Você já possui esse cargo.",
                ephemeral=True
            )
            return

        canal_id = config.get("role_request_channel_id")
        canal = guild.get_channel(canal_id) if canal_id else None

        if canal is None:
            await interaction.response.send_message(
                "Canal de solicitação de cargos não encontrado no config.json.",
                ephemeral=True
            )
            return
        

        pending[str(interaction.user.id)] = {
            "user_id": interaction.user.id,
            "role_id": role_id,
            "role_name": role.name,
            "motivo": self.motivo,
            "created_at": datetime.now().timestamp()
        }
        
        await send_log_embed(
            guild,
            "🎭 Novo pedido de cargo",
            (
                f"**Usuário:** {interaction.user.mention}\n"
                f"**Cargo solicitado:** {role.name}\n"
                f"**Motivo:** {self.motivo}"
            ),
            discord.Color.blurple()
        )
        
        persist_storage()

        embed = discord.Embed(
            title="Novo Pedido de Cargo",
            color=get_embed_color()
        )
        embed.add_field(name="Usuário", value=interaction.user.mention, inline=False)
        embed.add_field(name="Cargo", value=role.name, inline=False)
        embed.add_field(name="Motivo", value=self.motivo, inline=False)

        await canal.send(
            embed=embed,
            view=RoleApprovalView(interaction.user.id)
        )

        await interaction.response.edit_message(
            content="Pedido enviado para aprovação.",
            view=None,
            delete_after=15
        )


class RoleRequestView(discord.ui.View):
    def __init__(self, user_id, motivo):
        super().__init__(timeout=300)
        self.add_item(RoleRequestSelect(user_id, motivo))


class RoleApproveButton(discord.ui.Button):
    def __init__(self, user_id):
        super().__init__(
            label="Aprovar Cargo",
            style=discord.ButtonStyle.success,
            custom_id=f"approve_role_request_{user_id}"
        )
        self.user_id = user_id

    async def callback(self, interaction):
        if not has_approver_role(interaction.user):
            await interaction.response.send_message(
                "Sem permissão.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Servidor não encontrado.",
                ephemeral=True
            )
            return

        data = get_pending_role_requests().get(str(self.user_id))
        if not data:
            await interaction.response.send_message(
                "Pedido não encontrado.",
                ephemeral=True
            )
            return

        member = guild.get_member(self.user_id)
        if member is None:
            await interaction.response.send_message(
                "Membro não encontrado.",
                ephemeral=True
            )
            return

        role = guild.get_role(data["role_id"])
        if role is None:
            await interaction.response.send_message(
                "Cargo não encontrado.",
                ephemeral=True
            )
            return

        try:
            await member.add_roles(role, reason="Pedido de cargo aprovado")
        except discord.Forbidden:
            await interaction.response.send_message(
                "O bot não conseguiu adicionar o cargo. Verifique permissões.",
                ephemeral=True
            )
            return

        approved_role_requests = get_approved_role_requests()
        approved_role_requests.append({
            "user_id": member.id,
            "discord_name": str(member),
            "role_name": role.name,
            "motivo": data["motivo"],
            "approved_by": str(interaction.user),
            "approved_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        })

        get_pending_role_requests().pop(str(self.user_id), None)
        persist_storage()
        
        await send_log_embed(
            guild,
            "✅ Cargo aprovado",
            (
                f"**Membro:** {member.mention}\n"
                f"**Cargo:** {role.name}\n"
                f"**Motivo informado:** {data['motivo']}\n"
                f"**Aprovado por:** {interaction.user.mention}"
            ),
            discord.Color.green()
        )

        await interaction.message.edit(
            content=f"Cargo aprovado por {interaction.user.mention}",
            embed=None,
            view=None
        )

        try:
            await member.send(
                f"Seu pedido de cargo em **{guild.name}** foi aprovado.\n"
                f"Cargo recebido: **{role.name}**"
            )
        except discord.HTTPException:
            pass


class RoleRejectButton(discord.ui.Button):
    def __init__(self, user_id):
        super().__init__(
            label="Recusar Cargo",
            style=discord.ButtonStyle.danger,
            custom_id=f"reject_role_request_{user_id}"
        )
        self.user_id = user_id

    async def callback(self, interaction):
        if not has_approver_role(interaction.user):
            await interaction.response.send_message(
                "Sem permissão.",
                ephemeral=True
            )
            return

        data = get_pending_role_requests().get(str(self.user_id))
        if not data:
            await interaction.response.send_message(
                "Pedido não encontrado.",
                ephemeral=True
            )
            return

        get_pending_role_requests().pop(str(self.user_id), None)
        persist_storage()
        
        if interaction.guild:
            await send_log_embed(
                interaction.guild,
                "❌ Cargo recusado",
                (
                    f"**Membro:** <@{self.user_id}>\n"
                    f"**Motivo do pedido:** {data['motivo']}\n"
                    f"**Recusado por:** {interaction.user.mention}"
                ),
                discord.Color.red()
            )

        await interaction.message.edit(
            content="Pedido de cargo recusado.",
            embed=None,
            view=None
        )


class RoleApprovalView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.add_item(RoleApproveButton(user_id))
        self.add_item(RoleRejectButton(user_id))


class RoleRequestButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Solicitar Cargo",
            style=discord.ButtonStyle.secondary,
            custom_id="role_request_button"
        )

    async def callback(self, interaction):
        await interaction.response.send_modal(RoleRequestModal())


class RoleRequestPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RoleRequestButton())


@bot.command(name="painelcargos")
async def painel_cargos(ctx):
    embed = discord.Embed(
        title="Solicitação de Cargos",
        description="Clique abaixo para solicitar um cargo.",
        color=get_embed_color()
    )

    await ctx.send(embed=embed, view=RoleRequestPanelView())


TICKET_MESSAGES = [
    "🖤 Ticket aberto. Agora me conta o caos com calma.",
    "👁️ O suporte ouviu seu chamado. Fale, criatura.",
    "☕ Beleza, abriu o ticket. Agora joga a dúvida na mesa.",
    "🩸 Mais um ticket nasceu. Espero que não seja incêndio.",
    "🎭 Atendimento Iconics ativado. Qual é o drama de hoje?"
]

FAQ_RESPONSES = {
    "registro": "📝 Pra se registrar, usa o painel de registro e clica em **Registrar**.",
    "cargo": "🎭 Pra pedir um cargo, usa o painel de cargos e manda um motivo decente.",
    "ticket": "🎟️ Você já está num ticket. Respira e explica sua dúvida direito.",
    "staff": "👑 Se eu não resolver, a staff entra em cena.",
    "aprovacao": "✅ As aprovações são feitas pela staff responsável."
}


class DeleteTicketButton(discord.ui.Button):
    def __init__(self, owner_id):
        super().__init__(
            label="Apagar Ticket",
            style=discord.ButtonStyle.danger,
            custom_id=f"delete_ticket_{owner_id}"
        )
        self.owner_id = owner_id

    async def callback(self, interaction):
        if not has_ticket_staff_role(interaction.user):
            await interaction.response.send_message(
                "Só a staff pode apagar tickets.",
                ephemeral=True
            )
            return

        open_tickets = get_open_tickets()
        open_tickets.pop(str(self.owner_id), None)
        
        get_ticket_ai_disabled().pop(str(interaction.channel.id), None)
        get_ticket_assumed_by().pop(str(interaction.channel.id), None)

        persist_storage()
        
        
        await send_log_embed(
            interaction.guild,
            "🗑️ Ticket apagado",
            (
                f"**Staff:** {interaction.user.mention}\n"
                f"**Dono do ticket:** <@{self.owner_id}>\n"
                f"**Canal:** {interaction.channel.name}"
            ),
            discord.Color.red()
        )

        await interaction.response.send_message("Apagando ticket...", ephemeral=True)
        await interaction.channel.delete()


class LockTicketButton(discord.ui.Button):
    def __init__(self, owner_id):
        super().__init__(
            label="Trancar Ticket",
            style=discord.ButtonStyle.secondary,
            custom_id=f"lock_ticket_{owner_id}"
        )
        self.owner_id = owner_id

    async def callback(self, interaction):
        if not has_ticket_staff_role(interaction.user):
            await interaction.response.send_message(
                "Só a staff pode trancar tickets.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Servidor não encontrado.",
                ephemeral=True
            )
            return

        member = guild.get_member(self.owner_id)
        if member is None:
            await interaction.response.send_message(
                "Dono do ticket não encontrado.",
                ephemeral=True
            )
            return

        await interaction.channel.set_permissions(
            member,
            view_channel=True,
            send_messages=False,
            add_reactions=False,
            read_message_history=True,
            attach_files=False,
            embed_links=False
        )
        
        await send_log_embed(
            guild,
            "🔒 Ticket trancado",
            (
                f"**Staff:** {interaction.user.mention}\n"
                f"**Dono do ticket:** {member.mention}\n"
                f"**Canal:** {interaction.channel.mention}"
            ),
            discord.Color.orange()
        )

        await interaction.response.send_message(
            f"Ticket trancado com sucesso por {interaction.user.mention}.",
            ephemeral=False
        )

        await interaction.channel.send(
            f"🔒 Ticket trancado.\n"
            f"**Assumido por:** {interaction.user.mention}\n"
            f"O dono do ticket não pode mais enviar mensagens."
        )


class UnlockTicketButton(discord.ui.Button):
    def __init__(self, owner_id):
        super().__init__(
            label="Destrancar Ticket",
            style=discord.ButtonStyle.success,
            custom_id=f"unlock_ticket_{owner_id}"
        )
        self.owner_id = owner_id

    async def callback(self, interaction):
        if not has_ticket_staff_role(interaction.user):
            await interaction.response.send_message(
                "Só a staff pode destrancar tickets.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Servidor não encontrado.",
                ephemeral=True
            )
            return

        member = guild.get_member(self.owner_id)
        if member is None:
            await interaction.response.send_message(
                "Dono do ticket não encontrado.",
                ephemeral=True
            )
            return

        await interaction.channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            add_reactions=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True
        )
        
        await send_log_embed(
            guild,
            "🔓 Ticket destrancado",
            (
                f"**Staff:** {interaction.user.mention}\n"
                f"**Dono do ticket:** {member.mention}\n"
                f"**Canal:** {interaction.channel.mention}"
            ),
            discord.Color.green()
        )

        await interaction.response.send_message(
            f"Ticket destrancado com sucesso por {interaction.user.mention}.",
            ephemeral=False
        )

        await interaction.channel.send(
            f"🔓 Ticket destrancado.\n"
            f"**Destrancado por:** {interaction.user.mention}\n"
            f"O dono do ticket pode voltar a enviar mensagens."
        )


class AssumeTicketButton(discord.ui.Button):
    def __init__(self, owner_id):
        super().__init__(
            label="Assumir Ticket",
            style=discord.ButtonStyle.primary,
            custom_id=f"assume_ticket_{owner_id}"
        )
        self.owner_id = owner_id

    async def callback(self, interaction):
        if not has_ticket_staff_role(interaction.user):
            await interaction.response.send_message(
                "Só a staff pode assumir tickets.",
                ephemeral=True
            )
            return
        
        get_ticket_ai_disabled()[str(interaction.channel.id)] = True
        get_ticket_assumed_by()[str(interaction.channel.id)] = interaction.user.id
        persist_storage()

        await interaction.response.send_message(
            f"{interaction.user.mention} assumiu este ticket.",
            ephemeral=False
        )
        
        await send_log_embed(
            interaction.guild,
            "👑 Ticket assumido",
            (
                f"**Staff:** {interaction.user.mention}\n"
                f"**Dono do ticket:** <@{self.owner_id}>\n"
                f"**Canal:** {interaction.channel.mention}"
            ),
            discord.Color.gold()
        )

        await interaction.channel.send(
            f"👑 **Staff responsável:** {interaction.user.mention}"
        )


class CloseOptionsView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=120)
        self.add_item(DeleteTicketButton(owner_id))
        self.add_item(LockTicketButton(owner_id))
        self.add_item(UnlockTicketButton(owner_id))


class CloseTicketButton(discord.ui.Button):
    def __init__(self, owner_id):
        super().__init__(
            label="Fechar Ticket",
            style=discord.ButtonStyle.danger,
            custom_id=f"close_ticket_{owner_id}"
        )
        self.owner_id = owner_id

    async def callback(self, interaction):
        if interaction.user.id != self.owner_id and not has_ticket_staff_role(interaction.user):
            await interaction.response.send_message(
                "Só o dono do ticket ou a staff pode fechar isso aqui.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "A staff decide o destino do ticket. Apagar, trancar ou destrancar?",
            ephemeral=True,
            view=CloseOptionsView(owner_id=self.owner_id)
        )


class TicketControlView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=None)
        self.add_item(AssumeTicketButton(owner_id))
        self.add_item(UnlockTicketButton(owner_id))
        self.add_item(CloseTicketButton(owner_id))


class OpenTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Abrir Ticket",
            style=discord.ButtonStyle.success,
            custom_id="open_ticket_button"
        )

    async def callback(self, interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Servidor não encontrado.",
                ephemeral=True
            )
            return

        open_tickets = get_open_tickets()

        existing_ticket_id = open_tickets.get(str(interaction.user.id))
        if existing_ticket_id:
            existing_channel = guild.get_channel(existing_ticket_id)
            if existing_channel:
                await interaction.response.send_message(
                    f"Você já tem um ticket aberto: {existing_channel.mention}",
                    ephemeral=True
                )
                return
            open_tickets.pop(str(interaction.user.id), None)
            persist_storage()

        category = guild.get_channel(config.get("ticket_category_id"))
        if category is None:
            await interaction.response.send_message(
                "Categoria de ticket não configurada no config.json.",
                ephemeral=True
            )
            return

        staff_role_id = config.get("ticket_staff_role_id")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )
        }

        if staff_role_id:
            staff_role = guild.get_role(staff_role_id)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )

        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name.lower()}-{interaction.user.id}",
            category=category,
            overwrites=overwrites,
            reason="Novo ticket aberto"
        )

        open_tickets[str(interaction.user.id)] = channel.id
        get_ticket_ai_disabled().pop(str(channel.id), None)
        get_ticket_assumed_by().pop(str(channel.id), None)
        
        await send_log_embed(
            guild,
            "🎟️ Ticket aberto",
            (
                f"**Usuário:** {interaction.user.mention}\n"
                f"**Canal:** {channel.mention}"
            ),
            discord.Color.blurple()
        )
        
        persist_storage()

        embed = discord.Embed(
            title="Ticket aberto",
            description=random.choice(TICKET_MESSAGES),
            color=get_embed_color()
        )
        embed.add_field(
            name="Como funciona",
            value=(
                "Explique sua dúvida aqui.\n"
                "Se eu conseguir responder sozinho, eu respondo.\n"
                "Se não, a staff assume o atendimento."
            ),
            inline=False
        )

        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=TicketControlView(owner_id=interaction.user.id)
        )

        await interaction.response.send_message(
            f"Seu ticket foi aberto em {channel.mention}",
            ephemeral=True
        )


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(OpenTicketButton())


@bot.command(name="painelticket")
async def painel_ticket(ctx):
    embed = discord.Embed(
        title="Central de Tickets",
        description="Clique no botão abaixo para abrir um ticket.",
        color=get_embed_color()
    )
    await ctx.send(embed=embed, view=TicketPanelView())
    

    
@bot.event
async def on_ready():
    bot.add_view(RegisterView())
    bot.add_view(RoleRequestPanelView())
    bot.add_view(TicketPanelView())

    for request in get_pending_requests().values():
        user_id = request.get("user_id")
        if user_id:
            try:
                bot.add_view(ApprovalView(target_user_id=int(user_id)))
            except Exception:
                pass

    for request in get_pending_role_requests().values():
        user_id = request.get("user_id")
        if user_id:
            try:
                bot.add_view(RoleApprovalView(user_id=int(user_id)))
            except Exception:
                pass

    for owner_id in list(get_open_tickets().keys()):
        try:
            bot.add_view(TicketControlView(owner_id=int(owner_id)))
        except Exception:
            pass

    if not sync_member_link_request_notifications.is_running():
        sync_member_link_request_notifications.start()
    if not sync_site_logs_to_discord.is_running():
        sync_site_logs_to_discord.start()
    if not sync_recruitment_submissions_to_discord.is_running():
        sync_recruitment_submissions_to_discord.start()
    if not sync_pending_site_link_requests_to_discord.is_running():
        sync_pending_site_link_requests_to_discord.start()

    print(f"Bot conectado como {bot.user} | ID: {bot.user.id}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # IA automática em ticket
    if is_ticket_channel(message.channel):
        channel_id = str(message.channel.id)

        # se staff assumiu, IA para
        if get_ticket_ai_disabled().get(channel_id):
            await process_commands_once(message)
            return

        # evita responder comando
        if message.content.startswith("!"):
            await process_commands_once(message)
            return

        # evita responder imagem/anexo - IA não suporta imagem
        if message.attachments and any(att.content_type and att.content_type.startswith("image/") for att in message.attachments):
            await message.channel.send(
                "📸 Hm... até eu, experimento 626, ainda não aprendi a ler imagens! 👽\n"
                "Me explica o que você quer com palavras? Así posso ajudar!"
            )
            return

        # evita responder mensagem vazia
        if not message.content.strip():
            await process_commands_once(message)
            return

        # FAQ primeiro
        lower_msg = message.content.lower()
        for palavra, resposta in FAQ_RESPONSES.items():
            if palavra in lower_msg:
                await message.channel.send(resposta)
                await process_commands_once(message)
                return

        # IA depois
        try:
            async with message.channel.typing():
                resposta = await ask_stitch_ai(message.content)

            await message.channel.send(resposta[:2000])

        except Exception as e:
            print("ERRO IA TICKET:", e)
            await message.channel.send(
                "⚠️ Hm... o cérebro alienígena travou aqui. "
                "Se precisar, chama a staff. grrr."
            )

    await process_commands_once(message)


@bot.command(name="aceitos")
async def listar_aceitos(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await send_auto_delete(ctx, "Você não tem permissão para ver os membros aceitos.")
        return

    approved_members = get_approved_members()

    if not approved_members:
        await send_auto_delete(ctx, "Ainda não há membros aprovados registrados.")
        return

    embed = discord.Embed(
        title="Membros aceitos",
        color=get_embed_color()
    )

    linhas = []
    for i, member_data in enumerate(reversed(approved_members[-15:]), start=1):
        linhas.append(
            f"**{i}.** {member_data['character_name']} | {member_data['character_id']} "
            f"- {member_data['requested_role_name']} "
            f"(recrutador: {member_data['recruiter_name']})"
        )

    embed.description = "\n".join(linhas)
    await send_auto_delete(ctx, embed=embed)


@bot.command(name="corrigirnicks", aliases=["corrigirnicknames"])
async def corrigir_nicks_aprovados(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await send_auto_delete(ctx, "Você não tem permissão para corrigir nicknames.")
        return

    if ctx.guild is None:
        await send_auto_delete(ctx, "Esse comando só pode ser usado dentro do servidor.")
        return

    approved_members = get_approved_members()
    if not approved_members:
        await send_auto_delete(ctx, "Ainda não há membros aprovados registrados.")
        return

    changed = 0
    already_ok = 0
    missing = 0
    failed = []

    await ctx.send("Corrigindo nicknames dos membros aprovados...")

    for member_data in approved_members:
        user_id = member_data.get("user_id")
        if not user_id:
            missing += 1
            continue

        member = await fetch_guild_member(ctx.guild, int(user_id))
        if not member:
            missing += 1
            continue

        desired_nick = format_registration_nickname(
            member_data.get("character_name"),
            member_data.get("character_id")
        )
        status = await update_member_nickname(
            member,
            desired_nick,
            f"Correção de nickname solicitada por {ctx.author}"
        )

        if status.startswith("alterado"):
            changed += 1
        elif status == "já estava correto":
            already_ok += 1
        else:
            failed.append(f"{member.mention}: {status}")

    embed = discord.Embed(
        title="Correção de nicknames",
        color=get_embed_color()
    )
    embed.add_field(name="Alterados", value=str(changed), inline=True)
    embed.add_field(name="Já corretos", value=str(already_ok), inline=True)
    embed.add_field(name="Não encontrados", value=str(missing), inline=True)

    if failed:
        embed.add_field(
            name="Falhas",
            value="\n".join(failed[:8]),
            inline=False
        )

    await ctx.send(embed=embed)


@bot.command(name="testarnick")
async def testar_nick(ctx, member: discord.Member, *, desired_nick: str):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await send_auto_delete(ctx, "Você não tem permissão para testar nicknames.")
        return

    if ctx.guild is None or ctx.guild.me is None:
        await send_auto_delete(ctx, "Servidor não encontrado.")
        return

    bot_member = ctx.guild.me
    can_manage_nicks = bot_member.guild_permissions.manage_nicknames
    hierarchy_ok = bot_member.top_role > member.top_role

    status = await update_member_nickname(
        member,
        desired_nick,
        f"Teste de nickname solicitado por {ctx.author}"
    )

    embed = discord.Embed(
        title="Teste de nickname",
        color=get_embed_color()
    )
    embed.add_field(name="Resultado", value=status, inline=False)
    embed.add_field(name="Permissão Gerenciar apelidos", value="sim" if can_manage_nicks else "não", inline=True)
    embed.add_field(name="Hierarquia OK", value="sim" if hierarchy_ok else "não", inline=True)
    embed.add_field(name="Maior cargo do bot", value=bot_member.top_role.mention, inline=False)
    embed.add_field(name="Maior cargo do membro", value=member.top_role.mention, inline=False)

    await ctx.send(embed=embed)


@bot.command(name="ping")
async def ping(ctx):
    await send_auto_delete(ctx, "pong")


@bot.event
async def on_member_remove(member):
    user_id = member.id

    pending = get_pending_requests()
    pending.pop(str(user_id), None)

    pending_role_requests = get_pending_role_requests()
    pending_role_requests.pop(str(user_id), None)

    open_tickets = get_open_tickets()
    open_tickets.pop(str(user_id), None)

    approved_members = get_approved_members()
    storage["approved_members"] = [
        approved for approved in approved_members
        if approved.get("user_id") != user_id
    ]

    persist_storage()

    await send_log_embed(
        member.guild,
        "🚪 Membro saiu do servidor",
        (
            f"**Membro:** {member.mention}\n"
            f"**Nome:** {member.name}\n"
            "Pendências, ticket aberto e registro anterior foram limpos."
        ),
        discord.Color.orange()
    )


@bot.command(name="insta")
@commands.cooldown(1, 10, commands.BucketType.user)
async def insta(ctx):
    embed = discord.Embed(
        title="📸 Instagram Iconics",
        description="Segue a gente lá pra ver os bastidores, eventos e conteúdos exclusivos 👀",
        color=get_embed_color()
    )
    embed.add_field(
        name="Link",
        value="https://www.instagram.com/frat.iconics/",
        inline=False
    )
    await ctx.send(embed=embed)


@bot.command(name="site")
@commands.cooldown(1, 10, commands.BucketType.user)
async def site(ctx):
    embed = discord.Embed(
        title="🌐 Site Oficial Iconics",
        description="Acesse nosso site oficial e faça parte da experiência completa.",
        color=get_embed_color()
    )
    embed.add_field(
        name="Link",
        value=config.get("iconics_form_url", "https://iconics-jade.vercel.app"),
        inline=False
    )
    await ctx.send(embed=embed)


@bot.command(name="tiktok")
@commands.cooldown(1, 10, commands.BucketType.user)
async def tiktok(ctx):
    embed = discord.Embed(
        title="🎵 TikTok Iconics",
        description="Conteúdos rápidos, caóticos e icônicos 🎭",
        color=get_embed_color()
    )
    embed.add_field(
        name="Link",
        value="https://www.tiktok.com/@iconics_frat",
        inline=False
    )
    await ctx.send(embed=embed)


@bot.command(name="humor")
@commands.cooldown(1, 10, commands.BucketType.user)
async def humor(ctx):
    frases = [
        "💀 Você não foi ignorado... só não é importante.",
        "🩸 A vida é difícil, mas você ajuda bastante.",
        "👁️ O problema não é você... mentira, é sim.",
        "🎭 Você tentou... e falhou lindamente.",
        "🔥 Continue assim e você vai longe... pra fora do servidor."
    ]
    await send_auto_delete(ctx, random.choice(frases))

@bot.command(name="sorte")
@commands.cooldown(1, 10, commands.BucketType.user)
async def sorte(ctx):
    numero = random.randint(1, 100)
    await send_auto_delete(ctx, f"🎲 Seu número da sorte é: **{numero}**")


@bot.command(name="ego")
@commands.cooldown(1, 10, commands.BucketType.user)
async def ego(ctx, member: discord.Member = None):
    member = member or ctx.author
    nivel = random.randint(0, 100)
    await send_auto_delete(ctx, f"📈 Nível de ego de {member.mention}: **{nivel}%**")


@bot.command(name="ship")
@commands.cooldown(1, 10, commands.BucketType.user)
async def ship(ctx, user1: discord.Member, user2: discord.Member):
    # evita ordem diferente do mesmo casal
    pair_ids = tuple(sorted([user1.id, user2.id]))

    # casais especiais com faixa fixa
    ship_overrides = {
        # exemplo:
        (407183729807720449, 1086790493280682005): (95, 100),
        (525055481140609024, 1086790493280682005): (5, 15)
    }

    if pair_ids in ship_overrides:
        min_pct, max_pct = ship_overrides[pair_ids]
        porcentagem = random.randint(min_pct, max_pct)
    else:
        porcentagem = random.randint(30, 85)

    if user1.id == user2.id:
        porcentagem = 100

    await send_auto_delete(ctx,
        f"💖 Compatibilidade entre {user1.mention} e {user2.mention}: **{porcentagem}%**"
    )


class DanceBindModal(discord.ui.Modal, title="Gerador de Bind de Dança"):
    key_input = discord.ui.TextInput(
        label="Tecla da bind",
        placeholder="ex: F6, K, NUMPAD1",
        default="F6",
        max_length=30,
        required=True
    )
    prefix_input = discord.ui.TextInput(
        label="Primeira parte",
        placeholder="ex: e3, e, e2, dance, anim",
        default="e3",
        max_length=30,
        required=True
    )
    dance_input = discord.ui.TextInput(
        label="Nome da dança",
        placeholder="ex: dancar23",
        default="dancar23",
        max_length=80,
        required=True
    )
    ids_input = discord.ui.TextInput(
        label="IDs",
        placeholder="3234 3214 ou 3234, 3214; 9999",
        default="3234\n3214",
        style=discord.TextStyle.paragraph,
        max_length=1200,
        required=True
    )

    def __init__(self, prefix="e3"):
        super().__init__()
        self.prefix_input.default = str(prefix or "e3")

    async def on_submit(self, interaction):
        await send_dance_bind_result(
            interaction,
            str(self.key_input.value),
            str(self.prefix_input.value),
            str(self.dance_input.value),
            str(self.ids_input.value),
            ephemeral=True
        )


class DanceBindPrefixButton(discord.ui.Button):
    def __init__(self, prefix):
        super().__init__(label=prefix, style=discord.ButtonStyle.secondary)
        self.prefix = prefix

    async def callback(self, interaction):
        await interaction.response.send_modal(DanceBindModal(prefix=self.prefix))


class DanceBindOpenButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Gerar bind", style=discord.ButtonStyle.primary)

    async def callback(self, interaction):
        await interaction.response.send_modal(DanceBindModal(prefix="e3"))


class DanceBindView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(DanceBindOpenButton())
        for prefix in ("e3", "e", "e2", "e4", "e5"):
            self.add_item(DanceBindPrefixButton(prefix))


@bot.command(name="painelbinddanca", aliases=["painelbind"])
@commands.cooldown(1, 10, commands.BucketType.user)
async def painel_bind_danca(ctx):
    embed = discord.Embed(
        title="Gerador de Bind de Dança FiveM",
        description=(
            "Clique em **Gerar bind** ou escolha um atalho de comando.\n"
            "Depois preencha tecla, comando, dança e os IDs."
        ),
        color=get_embed_color()
    )
    embed.add_field(
        name="Exemplo",
        value='`bind keyboard "F6" "e3 dancar23 3234; e3 dancar23 3214"`',
        inline=False
    )
    await ctx.send(embed=embed, view=DanceBindView())


@bot.command(name="binddanca", aliases=["bind", "danca"])
@commands.cooldown(1, 5, commands.BucketType.user)
async def bind_danca(ctx, key: str = None, prefix: str = None, dance: str = None, *, ids: str = None):
    if not key or not prefix or not dance or not ids:
        await ctx.send(
            "Uso: `!binddanca <tecla> <comando> <danca> <ids>`\n"
            "Exemplo: `!binddanca F6 e3 dancar23 3234 3214`"
        )
        return

    await send_dance_bind_result(ctx, key, prefix, dance, ids)


@bot.command(name="help")
@commands.cooldown(1, 10, commands.BucketType.user)
async def help_command(ctx):
    member = ctx.author if isinstance(ctx.author, discord.Member) else None
    is_staff = bool(member and has_approver_role(member))
    can_hierarchy = bool(
        member
        and (
            is_supreme_member(member)
            or can_use_command(member, "sethierarquia")
            or can_use_command(member, "removerhierarquia")
            or can_use_command(member, "permcomando")
        )
    )

    sections = {
        "🧠 IA e Memória": [],
        "🔗 Vinculo de Card": [],
        "🌐 Site (Admin)": [],
        "🤝 Parcerias": [],
        "👑 Hierarquia e Permissões": [],
        "⚙️ Sistema": [],
        "🗑️ Auto-Delete": [],
        "🌐 Redes": [],
        "🎮 FiveM": [],
        "🎭 Diversão": [],
    }

    # Públicos
    sections["🧠 IA e Memória"] += [
        "`!ia <pergunta>` → conversa com o Stitch",
        "`!conhecimento <chave>` → mostra o que ele sabe",
        "`!listarknowledge [categoria]` → lista memória",
        "`!debugmemoria <pergunta>` → mostra o que ele encontrou",
    ]
    sections["🔗 Vinculo de Card"] += [
        "`!vincularsite <codigo>` → vincula seu Discord à conta do site",
        "`!solicitarvinculo <id> <codigo>` → solicita vínculo do seu Discord ao card",
        "`!statusvinculo` → mostra status das suas solicitações",
        "`!meuvinculo` → mostra seu vínculo ativo",
        "`!desvincularmeu` → remove seu vínculo ativo",
        "`!editarmeumembro` → editor em 3 etapas do seu card vinculado",
        "`!editarmeumembro <campo> | <valor>` → atalho de edição rápida",
        "`!vergaleriameu` → lista imagens da sua galeria com IDs",
        "`!addfotomeu` → adiciona fotos na sua galeria via anexo",
        "`!removerfotomeu <indice>` → remove foto da sua galeria por ID",
    ]
    sections["🌐 Redes"] += [
        "`!insta` → instagram da Iconics",
        "`!site` → site oficial",
        "`!tiktok` → tiktok da Iconics",
    ]
    sections["🤝 Parcerias"] += [
        "`!parcerias` → ver parcerias da Iconics",
    ]
    sections["🎮 FiveM"] += [
        "`!painelbinddanca` → abre o gerador interativo de bind de dança",
        "`!binddanca <tecla> <comando> <danca> <ids>` → gera a bind direto",
    ]
    sections["🎭 Diversão"] += [
        "`!humor` → frase aleatória",
        "`!sorte` → número da sorte",
        "`!ship @user1 @user2` → compatibilidade",
        "`!ego [@user]` → nível de ego",
    ]
    sections["⚙️ Sistema"] += [
        "`!ping` → testa se o bot está online",
        "`!rankfrat` → mostra o ranking atual das fraternidades",
    ]

    # Staff/aprovadores
    if is_staff:
        sections["🧠 IA e Memória"] += [
            "`!ensinar chave | aliases | categoria | conteúdo` → ensina algo",
            "`!recarregarknowledge` → recarrega o knowledge.json",
        ]
        sections["🌐 Site (Admin)"] += [
            "`!criarevento` → criar evento no site",
            "`!criarmembro` → cadastrar card de membro (staff)",
            "`!editarmembro <id>` → editar qualquer card (3 etapas)",
            "`!formulariomembro` → formulário para membros",
            "`!pendentesmembro` → ver formulários pendentes (staff)",
            "`!listareventos` → listar eventos",
            "`!listarmembros` → listar membros",
            "`!deletarevento <id>` → deletar evento",
            "`!deletarmembro <id>` → deletar membro",
            "`!adicionarfotomembro` → adicionar fotos à galeria do membro",
            "`!vergaleriamembro <id>` → ver galeria do membro",
            "`!removerfotomembro <id> <n>` → remover foto do membro",
            "`!addfraternidade <nome> | <pontos>` → adiciona fraternidade no ranking",
            "`!removerfraternidade <id>` → remove fraternidade do ranking",
            "`!somarpontosfrat <id> <valor>` → soma/remove pontos da fraternidade",
            "`!definirpontosfrat <id> <valor>` → define pontuação exata",
            "`!editarfraternidade <id> <campo> <valor>` → edita nome/foguete/cor/pontos",
        ]
        sections["🔗 Vinculo de Card"] += [
            "`!definircodigomembro <id> <codigo>` → define código do card",
            "`!pendenciasvinculo` → lista solicitações pendentes",
            "`!aprovarvinculo <id_solicitacao>` → aprova solicitação",
            "`!rejeitarvinculo <id_solicitacao> [motivo]` → rejeita solicitação",
            "`!desvincularmembro <id_card|discord_id|profile_id>` → revoga vínculo ativo",
        ]
        sections["🤝 Parcerias"] += [
            "`!parcerias` → ver parcerias da Iconics",
            "`!criarparceria` → criar nova parceria",
            "`!editarparceria` → editar parceria existente",
            "`!listarparcerias` → listar todas as parcerias",
            "`!deletarparceria <id>` → deletar parceria",
            "`!adicionarfotoparceria` → adicionar fotos à galeria",
            "`!vergaleriaparceria <id>` → ver galeria de fotos",
            "`!removerfotoparceria <id> <n>` → remover foto da galeria",
        ]
        sections["⚙️ Sistema"] += [
            "`!painelregistro` → painel de registro",
            "`!painelcargos` → painel de solicitação de cargos",
            "`!painelticket` → painel de tickets",
            "`!aceitos` → mostra membros aceitos",
            "`!ping` → testa se o bot está online",
        ]
        sections["🗑️ Auto-Delete"] += [
            "`!autodelete on/off` → liga/desliga auto-delete",
            "`!tempodelete <segundos>` → tempo para mensagens sumirem (5-300s)",
            "`!autodeletecomando <comando> on/off` → define auto-delete por comando",
            "`!listarautodelete` → lista comandos com auto-delete",
        ]

    if can_hierarchy:
        sections["👑 Hierarquia e Permissões"] += [
            "`!setsupremo @cargo` → define cargo supremo",
            "`!sethierarquia @cargo <nivel>` → define nível do cargo (1-10)",
            "`!removerhierarquia @cargo` → remove cargo da hierarquia",
            "`!permcomando @cargo <comando> <sim/nao>` → permissão de comando",
            "`!listarhierarquia` → mostra hierarquia configurada",
            "`!listarpermissoes [comando]` → lista permissões de comandos",
            "`!meunivel` → mostra seu nível hierárquico",
        ]

    embed = discord.Embed(
        title="📜 Comandos Iconics",
        description="Comandos visíveis para o seu cargo atual.",
        color=get_embed_color()
    )

    visible_any = False
    for section_name, lines in sections.items():
        if not lines:
            continue
        visible_any = True
        embed.add_field(name=section_name, value="\n".join(lines[:25]), inline=False)

    if not visible_any:
        embed.description = "Nenhum comando disponível para o seu cargo no momento."

    await ctx.send(embed=embed)


@bot.event
async def on_command_error(ctx, error):
    original_send = getattr(ctx, "_autodelete_original_send", None)
    original_reply = getattr(ctx, "_autodelete_original_reply", None)
    if original_send:
        ctx.send = original_send
    if original_reply:
        ctx.reply = original_reply

    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(
            f"⏳ Calma aí, {ctx.author.mention}. Espera **{error.retry_after:.1f}s** para usar esse comando de novo."
        )
        return

    raise error


@bot.command(name="ia")
@commands.cooldown(1, 10, commands.BucketType.user)
async def ia(ctx, *, pergunta: str = None):
    if not pergunta:
        await ctx.send("Uso: `!ia sua pergunta`")
        return

    # impede imagem - IA não suporta
    if ctx.message and ctx.message.attachments and any(
        att.content_type and att.content_type.startswith("image/") 
        for att in ctx.message.attachments
    ):
        await ctx.send(
            "📸 Hm... até eu, experimento 626, ainda não aprendi a ler imagens! 👽\n"
            "Me explica o que você quer com palavras? Así posso ajudar!"
        )
        return

    async with ctx.typing():
        try:
            resposta = await ask_stitch_ai(pergunta)

            if len(resposta) > 1900:
                resposta = resposta[:1900] + "..."

            # IA NÃO usa auto-delete (mensagem permanente)
            await ctx.send(resposta)

        except Exception as e:
            print("ERRO IA:", e)

            contexto = get_knowledge_context(pergunta)

            if contexto and "Nenhum conhecimento específico encontrado." not in contexto:
                # IA NÃO usa auto-delete (mensagem permanente)
                await ctx.send(
                    "⚠️ Hm... meu cérebro alienígena ficou meio tostado agora, grrr 😵\n"
                    "Mas eu ainda lembro disso aqui, olha só:\n\n"
                    f"{contexto}\n\n"
                    "Hihi... Experimento 626 pode até tropeçar, mas ainda sabe das coisas. 👽✨"
                )
            else:
                # IA NÃO usa auto-delete (mensagem permanente)
                await ctx.send(
                    "⚠️ Grrr... meus neurônios espaciais deram cambalhota e eu não achei nada útil na memória 😵‍💫\n"
                    "Tenta de novo mais tarde ou ensina isso pra mim, vai. Stitch aprende... às vezes. 👽🔧"
                )

    
@bot.command(name="esquecer")
@commands.cooldown(1, 10, commands.BucketType.user)
async def esquecer(ctx, chave: str = None):
    if not has_approver_role(ctx.author):
        await send_auto_delete(ctx, "❌ Só a staff pode me fazer esquecer.")
        return

    if not chave:
        await send_auto_delete(ctx, "Uso: `!esquecer chave`")
        return

    key = normalize_key(chave)

    if key not in knowledge:
        await send_auto_delete(ctx, f"🤷 Não sei nada sobre **{key}** pra esquecer.")
        return

    knowledge.pop(key, None)
    save_knowledge(knowledge)

    await send_auto_delete(ctx, f"🫠 Pronto, esqueci **{key}**.")


@bot.command(name="procurar")
@commands.cooldown(1, 10, commands.BucketType.user)
async def procurar(ctx, *, pergunta: str = None):
    if not pergunta:
        await send_auto_delete(ctx, "Uso: `!procurar pergunta`")
        return

    contexto = get_knowledge_context(pergunta)

    await send_auto_delete(ctx,
        f"🔎 O que eu encontrei na memória:\n```{contexto[:1800]}```"
    )
    

@bot.command(name="listarknowledge")
@commands.cooldown(1, 10, commands.BucketType.user)
async def listarknowledge(ctx, categoria: str = None):
    if not knowledge:
        await send_auto_delete(ctx, "📭 Minha cabeça tá vazia no momento.")
        return

    if not categoria:
        keys = ", ".join(sorted(knowledge.keys()))
        await send_auto_delete(ctx, f"🧠 Eu sei sobre: {keys}")
        return

    categoria = normalize_text(categoria)
    filtrados = []

    for key, data in knowledge.items():
        if isinstance(data, dict) and normalize_text(data.get("category", "")) == categoria:
            filtrados.append(key)

    if not filtrados:
        await send_auto_delete(ctx, f"🤷 Não sei nada na categoria **{categoria}**.")
        return

    await send_auto_delete(ctx,
        f"🧠 Categoria **{categoria}**: {', '.join(sorted(filtrados))}"
    )
    
@bot.command(name="conhecimento")
@commands.cooldown(1, 10, commands.BucketType.user)
async def conhecimento(ctx, chave: str = None):
    if not chave:
        await send_auto_delete(ctx, "Uso: `!conhecimento chave`")
        return

    key = normalize_key(chave)

    if key not in knowledge:
        await send_auto_delete(ctx, f"🤔 Ainda não sei nada sobre **{key}**.")
        return

    data = knowledge[key]

    if isinstance(data, str):
        await send_auto_delete(ctx, f"📚 **{key}**: {data}")
        return

    aliases = ", ".join(data.get("aliases", [])) or "nenhum"
    category = data.get("category", "geral")
    content = data.get("content", "sem conteúdo")

    await send_auto_delete(ctx,
        f"📚 **{key}**\n"
        f"**Categoria:** {category}\n"
        f"**Aliases:** {aliases}\n"
        f"**Conteúdo:** {content}"
    )
    
@bot.command(name="ensinar")
@commands.cooldown(1, 10, commands.BucketType.user)
async def ensinar(ctx, *, texto: str = None):
    if not has_approver_role(ctx.author):
        await send_auto_delete(ctx, "❌ Só a staff pode me ensinar coisas novas.")
        return

    if not texto or "|" not in texto:
        await send_auto_delete(ctx,
            "Uso: `!ensinar chave | alias1,alias2 | categoria | conteúdo`\n"
            "Exemplo: `!ensinar mika | mika,mikaela | membro | Mika é importante na Iconics.`"
        )
        return

    partes = [p.strip() for p in texto.split("|", 3)]

    if len(partes) < 4:
        await send_auto_delete(ctx,
            "Uso: `!ensinar chave | alias1,alias2 | categoria | conteúdo`"
        )
        return

    chave, aliases_raw, categoria, conteudo = partes
    key = normalize_key(chave)
    aliases = [normalize_text(a) for a in aliases_raw.split(",") if a.strip()]

    knowledge[key] = {
        "aliases": aliases,
        "category": normalize_text(categoria),
        "content": conteudo.strip()
    }

    save_knowledge(knowledge)

    await send_auto_delete(ctx,
        f"🧠 Hihi... aprendi sobre **{key}** na categoria **{categoria}**."
    )
    

@bot.command(name="recarregarknowledge")
@commands.cooldown(1, 10, commands.BucketType.user)
async def recarregarknowledge(ctx):
    if not has_approver_role(ctx.author):
        await send_auto_delete(ctx, "❌ Só a staff pode recarregar minha memória.")
        return

    global knowledge
    knowledge = load_knowledge()

    await send_auto_delete(ctx, "🧠 Hihi... memória recarregada com sucesso.")
    

@bot.command(name="debugmemoria")
@commands.cooldown(1, 10, commands.BucketType.user)
async def debugmemoria(ctx, *, pergunta: str = None):
    if not pergunta:
        await send_auto_delete(ctx, "Uso: `!debugmemoria sua pergunta`")
        return

    contexto = get_knowledge_context(pergunta)

    await send_auto_delete(ctx, f"🔎 Memória encontrada:\n```{contexto[:1800]}```")


# =========================
# COMANDOS DE HIERARQUIA E PERMISSÕES
# =========================

@bot.command(name="setsupremo")
@commands.cooldown(1, 5, commands.BucketType.user)
async def set_supremo(ctx, role: discord.Role = None):
    """Define o cargo supremo (acesso total ao bot) - Apenas para administradores atuais"""
    # Verifica se quem está executando tem permissão atual
    if not has_approver_role(ctx.author):
        await send_auto_delete(ctx, "❌ Apenas administradores podem definir o cargo supremo.")
        return
    
    if not role:
        current = get_supreme_role_id()
        if current:
            await send_auto_delete(ctx, f"📌 Cargo supremo atual: \u003c@\u0026{current}\u003e")
        else:
            await send_auto_delete(ctx, "📌 Nenhum cargo supremo definido. Use: `!setsupremo @cargo`")
        return
    
    set_supreme_role_id(role.id)
    await send_auto_delete(ctx, f"✅ Cargo supremo definido: {role.mention}\n\n⚠️ **Atenção:** Membros com este cargo terão acesso TOTAL ao bot!")


@bot.command(name="sethierarquia")
@commands.cooldown(1, 5, commands.BucketType.user)
async def set_hierarquia(ctx, role: discord.Role = None, level: int = None):
    """Define o nível hierárquico de um cargo (1-10)"""
    if not can_use_command(ctx.author, "sethierarquia"):
        await send_auto_delete(ctx, "❌ Você não tem permissão para usar este comando.")
        return
    
    if not role or level is None:
        await send_auto_delete(ctx, "Uso: `!sethierarquia @cargo \u003cnível\u003e` (nível 1-10)")
        return
    
    if level < 1 or level > 10:
        await send_auto_delete(ctx, "❌ O nível deve ser entre 1 e 10.")
        return
    
    set_role_hierarchy(role.id, level)
    await send_auto_delete(ctx, f"✅ Cargo {role.mention} definido como nível **{level}** na hierarquia.")


@bot.command(name="removerhierarquia")
@commands.cooldown(1, 5, commands.BucketType.user)
async def remover_hierarquia(ctx, role: discord.Role = None):
    """Remove um cargo da hierarquia"""
    if not can_use_command(ctx.author, "removerhierarquia"):
        await send_auto_delete(ctx, "❌ Você não tem permissão para usar este comando.")
        return
    
    if not role:
        await send_auto_delete(ctx, "Uso: `!removerhierarquia @cargo`")
        return
    
    remove_role_hierarchy(role.id)
    await send_auto_delete(ctx, f"✅ Cargo {role.mention} removido da hierarquia.")


@bot.command(name="permcomando")
@commands.cooldown(1, 5, commands.BucketType.user)
async def perm_comando(ctx, role: discord.Role = None, comando: str = None, permitir: str = None):
    """Define permissão de um cargo para usar um comando específico"""
    if not can_use_command(ctx.author, "permcomando"):
        await send_auto_delete(ctx, "❌ Você não tem permissão para usar este comando.")
        return
    
    if not role or not comando or permitir is None:
        await send_auto_delete(ctx, "Uso: `!permcomando @cargo \u003ccomando\u003e \u003csim/nao\u003e`")
        await send_auto_delete(ctx, "Exemplo: `!permcomando @Moderador ban sim`")
        return
    
    permitir = permitir.lower() in ["sim", "s", "yes", "y", "true", "1"]
    
    set_command_permission(role.id, comando, permitir)
    
    status = "✅ permitido" if permitir else "❌ negado"
    await send_auto_delete(ctx, f"Comando `!{comando}` {status} para o cargo {role.mention}.")


@bot.command(name="listarhierarquia")
@commands.cooldown(1, 10, commands.BucketType.user)
async def listar_hierarquia(ctx):
    """Mostra a hierarquia de cargos configurada"""
    embed = discord.Embed(
        title="👑 Hierarquia de Cargos",
        color=get_embed_color()
    )
    
    # Cargo supremo
    supreme_id = get_supreme_role_id()
    if supreme_id:
        supreme_role = ctx.guild.get_role(supreme_id)
        if supreme_role:
            embed.add_field(
                name="⚡ Cargo Supremo",
                value=supreme_role.mention,
                inline=False
            )
    
    # Hierarquia por níveis
    hierarchy = get_role_hierarchy()
    if hierarchy:
        # Ordena por nível (maior primeiro)
        sorted_roles = sorted(hierarchy.items(), key=lambda x: x[1], reverse=True)
        
        hierarquia_texto = []
        for role_id, level in sorted_roles:
            role = ctx.guild.get_role(int(role_id))
            if role:
                hierarquia_texto.append(f"Nível {level}: {role.mention}")
        
        if hierarquia_texto:
            embed.add_field(
                name="📊 Níveis Hierárquicos",
                value="\n".join(hierarquia_texto) or "Nenhum cargo configurado",
                inline=False
            )
    
    # Nível do usuário
    if isinstance(ctx.author, discord.Member):
        user_level = get_member_highest_level(ctx.author)
        is_supreme = is_supreme_member(ctx.author)
        
        if is_supreme:
            embed.add_field(
                name="🎯 Seu Nível",
                value="⚡ Cargo Supremo (Acesso Total)",
                inline=False
            )
        elif user_level > 0:
            embed.add_field(
                name="🎯 Seu Nível",
                value=f"Nível {user_level}",
                inline=False
            )
    
    await send_auto_delete(ctx, embed=embed)


@bot.command(name="listarpermissoes")
@commands.cooldown(1, 10, commands.BucketType.user)
async def listar_permissoes(ctx, comando: str = None):
    """Lista as permissões de comandos configuradas"""
    perms = get_command_permissions()
    
    if comando:
        # Mostra permissões de um comando específico
        command_perms = perms.get(comando, {})
        
        if not command_perms:
            await send_auto_delete(ctx, f"📭 Nenhuma permissão específica configurada para `!{comando}`.")
            return
        
        embed = discord.Embed(
            title=f"🔐 Permissões: !{comando}",
            color=get_embed_color()
        )
        
        permissoes_texto = []
        for role_id, allowed in command_perms.items():
            role = ctx.guild.get_role(int(role_id))
            if role:
                status = "✅" if allowed else "❌"
                permissoes_texto.append(f"{status} {role.mention}")
        
        embed.description = "\n".join(permissoes_texto) or "Nenhuma permissão configurada"
        await send_auto_delete(ctx, embed=embed)
    
    else:
        # Lista todos os comandos com permissões
        if not perms:
            await send_auto_delete(ctx, "📭 Nenhuma permissão de comando configurada.")
            return
        
        embed = discord.Embed(
            title="🔐 Permissões de Comandos",
            color=get_embed_color()
        )
        
        for cmd_name, cmd_perms in list(perms.items())[:10]:  # Limita a 10 comandos
            permissoes_texto = []
            for role_id, allowed in cmd_perms.items():
                role = ctx.guild.get_role(int(role_id))
                if role:
                    status = "✅" if allowed else "❌"
                    permissoes_texto.append(f"{status} {role.name}")
            
            if permissoes_texto:
                embed.add_field(
                    name=f"!{cmd_name}",
                    value="\n".join(permissoes_texto[:5]),  # Limita a 5 cargos por comando
                    inline=True
                )
        
        await send_auto_delete(ctx, embed=embed)


@bot.command(name="meunivel")
@commands.cooldown(1, 10, commands.BucketType.user)
async def meu_nivel(ctx):
    """Mostra seu nível hierárquico no bot"""
    if not isinstance(ctx.author, discord.Member):
        await send_auto_delete(ctx, "❌ Não foi possível verificar seu nível.")
        return
    
    is_supreme = is_supreme_member(ctx.author)
    user_level = get_member_highest_level(ctx.author)
    
    embed = discord.Embed(
        title=f"👤 {ctx.author.display_name}",
        color=get_embed_color()
    )
    
    if is_supreme:
        embed.add_field(
            name="⚡ Cargo",
            value="**Supremo** (Acesso Total)",
            inline=False
        )
    elif user_level > 0:
        embed.add_field(
            name="📊 Nível Hierárquico",
            value=f"**{user_level}**/10",
            inline=False
        )
    else:
        embed.add_field(
            name="📊 Nível Hierárquico",
            value="Nenhum (Membro comum)",
            inline=False
        )
    
    # Lista cargos do usuário na hierarquia
    hierarchy = get_role_hierarchy()
    user_roles = []
    for role in ctx.author.roles:
        if str(role.id) in hierarchy:
            user_roles.append(f"{role.mention} (Nível {hierarchy[str(role.id)]})")
    
    if user_roles:
        embed.add_field(
            name="🎭 Seus Cargos na Hierarquia",
            value="\n".join(user_roles),
            inline=False
        )
    
    await send_auto_delete(ctx, embed=embed)


# =========================
# COMANDOS DE AUTO-DELETE
# =========================

@bot.command(name="autodelete")
@commands.cooldown(1, 5, commands.BucketType.user)
async def auto_delete_cmd(ctx, status: str = None):
    """Liga/desliga o auto-delete de mensagens"""
    if not can_use_command(ctx.author, "autodelete"):
        await ctx.send("❌ Você não tem permissão para usar este comando.")
        return
    
    config = get_auto_delete_config()
    
    if status is None:
        status_text = "✅ Ligado" if config.get("enabled", False) else "❌ Desligado"
        await ctx.send(f"🗑️ Auto-delete: {status_text} | Tempo: {config.get('delay_seconds', 30)}s")
        return
    
    status = status.lower()
    if status in ["on", "ligar", "sim", "s", "true", "1"]:
        set_auto_delete_enabled(True)
        await ctx.send(f"✅ Auto-delete **ligado**! Mensagens sem botões serão apagadas após {config.get('delay_seconds', 30)}s.")
    elif status in ["off", "desligar", "nao", "n", "false", "0"]:
        set_auto_delete_enabled(False)
        await ctx.send("❌ Auto-delete **desligado**.")
    else:
        await ctx.send("Uso: `!autodelete on/off` ou `!autodelete ligar/desligar`")


@bot.command(name="tempodelete")
@commands.cooldown(1, 5, commands.BucketType.user)
async def tempo_delete(ctx, segundos: int = None):
    """Define o tempo em segundos para mensagens sumirem (5-300s)"""
    if not can_use_command(ctx.author, "tempodelete"):
        await ctx.send("❌ Você não tem permissão para usar este comando.")
        return
    
    if segundos is None:
        config = get_auto_delete_config()
        await ctx.send(f"⏱️ Tempo atual: {config.get('delay_seconds', 30)}s")
        return
    
    if segundos < 5 or segundos > 300:
        await ctx.send("❌ O tempo deve ser entre **5 e 300 segundos**.")
        return
    
    set_auto_delete_delay(segundos)
    await ctx.send(f"✅ Tempo de auto-delete definido para **{segundos} segundos**.")


@bot.command(name="autodeletecomando")
@commands.cooldown(1, 5, commands.BucketType.user)
async def auto_delete_comando(ctx, comando: str = None, status: str = None):
    """Define se um comando específico terá auto-delete"""
    if not can_use_command(ctx.author, "autodeletecomando"):
        await ctx.send("❌ Você não tem permissão para usar este comando.")
        return
    
    if not comando:
        await ctx.send("Uso: `!autodeletecomando <comando> on/off`")
        return
    
    # Remove o ! e normaliza
    comando = comando.lstrip("!").strip().lower()
    
    if status is None:
        # Mostra status atual
        tem_auto_delete = get_command_auto_delete(comando)
        status_text = "✅ Ligado" if tem_auto_delete else "❌ Desligado"
        await ctx.send(f"🗑️ Auto-delete para `!{comando}`: {status_text}")
        return
    
    status = status.lower()
    if status in ["on", "ligar", "sim", "s", "true", "1"]:
        set_command_auto_delete(comando, True)
        await ctx.send(f"✅ Auto-delete **ligado** para o comando `!{comando}`.")
    elif status in ["off", "desligar", "nao", "n", "false", "0"]:
        set_command_auto_delete(comando, False)
        await ctx.send(f"❌ Auto-delete **desligado** para o comando `!{comando}`.")
    else:
        await ctx.send("Uso: `!autodeletecomando <comando> on/off`")


@bot.command(name="listarautodelete")
@commands.cooldown(1, 10, commands.BucketType.user)
async def listar_auto_delete(ctx):
    """Lista os comandos com auto-delete configurado"""
    config = get_auto_delete_config()
    commands_config = list_auto_delete_commands()
    
    embed = discord.Embed(
        title="🗑️ Configuração de Auto-Delete",
        color=get_embed_color()
    )
    
    # Status global
    global_status = "✅ Ligado" if config.get("enabled", False) else "❌ Desligado"
    embed.add_field(
        name="Status Global",
        value=f"{global_status} | Tempo: {config.get('delay_seconds', 30)}s",
        inline=False
    )
    
    # Comandos configurados
    if commands_config:
        ligados = []
        desligados = []
        
        for cmd, enabled in sorted(commands_config.items()):
            if enabled:
                ligados.append(f"`!{cmd}`")
            else:
                desligados.append(f"`!{cmd}`")
        
        if ligados:
            embed.add_field(
                name="✅ Com Auto-Delete",
                value=", ".join(ligados[:15]) or "Nenhum",
                inline=False
            )
        
        if desligados:
            embed.add_field(
                name="❌ Sem Auto-Delete",
                value=", ".join(desligados[:15]) or "Nenhum",
                inline=False
            )
    else:
        embed.add_field(
            name="Comandos",
            value="Nenhum comando configurado ainda. Use `!autodeletecomando <comando> on`.",
            inline=False
        )
    
    await ctx.send(embed=embed)


import re
import uuid

def is_valid_uuid(val):
    if not val:
        return False
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False

# =========================
# SUPABASE INTEGRATION
# =========================
try:
    from supabase import create_client as supabase_create_client
    _sb_url = choose_supabase_url()
    _sb_key = choose_configured_secret("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key")
    _sb_admin_uuid_raw = choose_configured_secret("SUPABASE_ADMIN_UUID", "supabase_admin_uuid")
    _sb_admin_uuid = _sb_admin_uuid_raw if is_valid_uuid(_sb_admin_uuid_raw) else None
    if looks_supabase_url(_sb_url) and looks_configured_secret(_sb_key):
        try:
            supabase_client = supabase_create_client(_sb_url, _sb_key)
            print(">>> SUPABASE CONECTADO <<<")
        except Exception as e:
            supabase_client = None
            print("ERRO SUPABASE:", e)
    else:
        supabase_client = None
        print(">>> SUPABASE NAO CONFIGURADO (faltam url ou key validas) <<<")
except ImportError:
    supabase_client = None
    _sb_admin_uuid = None
    print(">>> SUPABASE NAO INSTALADO (pip install supabase) <<<")

def require_supabase():
    return supabase_client

_member_sessions = {}
_member_edit_sessions = {}

# =========================
# CRIAR EVENTO
# =========================
class CreateEventModal(discord.ui.Modal, title="Criar Evento"):
    titulo_input = discord.ui.TextInput(label="Título", max_length=100, required=True)
    data_input = discord.ui.TextInput(label="Data (AAAA-MM-DD)", placeholder="2026-05-20", max_length=10, required=True)
    horario_input = discord.ui.TextInput(label="Horário", placeholder="22h", max_length=20, required=False)
    local_input = discord.ui.TextInput(label="Local", placeholder="Mansão Iconics", max_length=100, required=False)
    descricao_input = discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, max_length=500, required=False)

    async def on_submit(self, interaction):
        sb = require_supabase()
        if not sb:
            await interaction.response.send_message("Supabase não configurado.", ephemeral=True)
            return
        data_str = str(self.data_input).strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", data_str):
            await interaction.response.send_message("Formato de data inválido. Use AAAA-MM-DD.", ephemeral=True)
            return
        try:
            datetime.strptime(data_str, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message("Data inválida.", ephemeral=True)
            return
        payload = {
            "titulo": str(self.titulo_input).strip(),
            "descricao": str(self.descricao_input).strip() or None,
            "data_evento": data_str,
            "horario": str(self.horario_input).strip() or None,
            "local": str(self.local_input).strip() or None,
            "imagem_url": None,
        }
        if _sb_admin_uuid:
            payload["criado_por"] = _sb_admin_uuid
        try:
            result = sb.table("events").insert(payload).execute()
            event_id = result.data[0]["id"] if result.data else None
        except Exception as e:
            await interaction.response.send_message(f"Erro ao criar evento: {e}", ephemeral=True)
            return
        embed = discord.Embed(title="Evento criado com sucesso!", color=discord.Color.green())
        embed.add_field(name="Título", value=payload["titulo"], inline=False)
        embed.add_field(name="Data", value=data_str, inline=True)
        embed.add_field(name="Horário", value=payload["horario"] or "—", inline=True)
        embed.add_field(name="Local", value=payload["local"] or "—", inline=False)
        view = AddEventImageView(event_id) if event_id else None
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        if interaction.guild:
            await send_log_embed(interaction.guild, "📅 Evento criado via Discord", f"**Título:** {payload['titulo']}\n**Data:** {data_str}\n**Por:** {interaction.user.mention}", discord.Color.green())

class AddEventImageButton(discord.ui.Button):
    def __init__(self, event_id):
        super().__init__(label="Adicionar Imagem", style=discord.ButtonStyle.secondary)
        self.event_id = event_id
    async def callback(self, interaction):
        await interaction.response.send_message("Envie a imagem como anexo na próxima mensagem (60s).", ephemeral=True)
        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and m.attachments
        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            url = msg.attachments[0].url
            sb = require_supabase()
            if sb:
                sb.table("events").update({"imagem_url": url}).eq("id", self.event_id).execute()
                await msg.reply("Imagem adicionada ao evento!", mention_author=False)
        except Exception:
            await interaction.followup.send("Tempo esgotado ou erro ao capturar imagem.", ephemeral=True)

class AddEventImageView(discord.ui.View):
    def __init__(self, event_id):
        super().__init__(timeout=120)
        self.add_item(AddEventImageButton(event_id))

class OpenCreateEventButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Criar Evento", style=discord.ButtonStyle.primary)
    async def callback(self, interaction):
        await interaction.response.send_modal(CreateEventModal())

class CreateEventView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(OpenCreateEventButton())

@bot.command(name="criarevento")
@commands.cooldown(1, 10, commands.BucketType.user)
async def criar_evento(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão para criar eventos.")
        return
    if not require_supabase():
        await ctx.send("Supabase não configurado.")
        return
    await ctx.send("Clique no botão para criar um evento:", view=CreateEventView())

# =========================
# CRIAR MEMBRO (3 MODAIS)
# =========================
class MemberModal1(discord.ui.Modal, title="Membro 1/3 - Dados Básicos"):
    nome_input = discord.ui.TextInput(label="Nome", max_length=50, required=True)
    idade_input = discord.ui.TextInput(label="Idade", max_length=3, required=False)
    cargo_input = discord.ui.TextInput(label="Cargo (membro/veterano/vice_lider/lider)", placeholder="membro", max_length=20, required=False, default="membro")
    meta_input = discord.ui.TextInput(label="Meta / subtítulo", max_length=100, required=False)
    personalidade_input = discord.ui.TextInput(label="Personalidade", style=discord.TextStyle.paragraph, max_length=500, required=False)
    async def on_submit(self, interaction):
        uid = interaction.user.id
        _member_sessions[uid] = _member_sessions.get(uid, {})
        _member_sessions[uid].update({
            "nome": str(self.nome_input).strip(),
            "idade": str(self.idade_input).strip() or None,
            "cargo": str(self.cargo_input).strip().lower() or "membro",
            "meta": str(self.meta_input).strip() or None,
            "personalidade": str(self.personalidade_input).strip() or None,
        })
        await interaction.response.send_message("Etapa 1/3 salva. Clique para continuar:", view=MemberStep2View(), ephemeral=True)

class MemberModal2(discord.ui.Modal, title="Membro 2/3 - Detalhes"):
    habitos_input = discord.ui.TextInput(label="Hábitos", style=discord.TextStyle.paragraph, max_length=500, required=False)
    gostos_input = discord.ui.TextInput(label="Gostos", style=discord.TextStyle.paragraph, max_length=500, required=False)
    hobbies_input = discord.ui.TextInput(label="Hobbies", max_length=200, required=False)
    tags_input = discord.ui.TextInput(label="Tags (separadas por |)", placeholder="frio | misterioso | elegante", max_length=200, required=False)
    stats_input = discord.ui.TextInput(label="Stats (Label:Valor | Label:Valor)", placeholder="Influência:10 | Presença:9", max_length=300, required=False)
    async def on_submit(self, interaction):
        uid = interaction.user.id
        _member_sessions[uid] = _member_sessions.get(uid, {})
        _member_sessions[uid].update({
            "habitos": str(self.habitos_input).strip() or None,
            "gostos": str(self.gostos_input).strip() or None,
            "hobbies": str(self.hobbies_input).strip() or None,
            "tags": str(self.tags_input).strip() or None,
            "stats": str(self.stats_input).strip() or None,
        })
        await interaction.response.send_message("Etapa 2/3 salva. Clique para finalizar:", view=MemberStep3View(), ephemeral=True)

class MemberModal3(discord.ui.Modal, title="Membro 3/3 - Visual"):
    sigil_input = discord.ui.TextInput(label="Sigil (símbolo)", placeholder="✦", max_length=5, required=False, default="✦")
    imagem_input = discord.ui.TextInput(label="URL da imagem (ou vazio)", max_length=500, required=False)
    cor_input = discord.ui.TextInput(label="Cor (hex ou: purple, gold, pink, cyan)", placeholder="#7c3aed", max_length=20, required=False, default="#7c3aed")
    ordem_input = discord.ui.TextInput(label="Ordem de exibição", placeholder="0", max_length=5, required=False, default="0")

    async def on_submit(self, interaction):
        uid = interaction.user.id
        session = _member_sessions.get(uid, {})
        session["sigil"] = str(self.sigil_input).strip() or "✦"
        session["imagem_url"] = str(self.imagem_input).strip() or None
        session["accent_color"] = str(self.cor_input).strip() or "#7c3aed"
        
        try:
            session["ordem"] = int(str(self.ordem_input).strip() or "0")
        except ValueError:
            session["ordem"] = 0
        
        sb = require_supabase()
        if not sb:
            await interaction.response.send_message("Supabase não configurado.", ephemeral=True)
            _member_sessions.pop(uid, None)
            return
        
        payload = {
            "nome": session.get("nome"),
            "idade": session.get("idade"),
            "cargo": session.get("cargo"),
            "meta": session.get("meta"),
            "personalidade": session.get("personalidade"),
            "habitos": session.get("habitos"),
            "gostos": session.get("gostos"),
            "hobbies": session.get("hobbies"),
            "tags": session.get("tags"),
            "sigil": session.get("sigil", "✦"),
            "accent_color": session.get("accent_color", "#7c3aed"),
            "stats": session.get("stats"),
            "imagem_url": session.get("imagem_url"),
            "ordem": session.get("ordem", 0),
            "galeria": session.get("galeria", []),
        }
        if _sb_admin_uuid:
            payload["criado_por"] = _sb_admin_uuid
        try:
            result = sb.table("member_cards").insert(payload).execute()
            member_id = result.data[0]["id"] if result.data else None
        except Exception as e:
            await interaction.response.send_message(f"Erro ao salvar membro: {e}", ephemeral=True)
            _member_sessions.pop(uid, None)
            return
        _member_sessions.pop(uid, None)
        embed = discord.Embed(title="Membro criado com sucesso!", color=discord.Color.green())
        embed.add_field(name="Nome", value=payload["nome"], inline=True)
        embed.add_field(name="Cargo", value=payload["cargo"], inline=True)
        embed.add_field(name="Cor", value=payload["accent_color"], inline=True)
        view = AddMemberImageView(member_id) if member_id and not payload["imagem_url"] else None
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        if interaction.guild:
            await send_log_embed(interaction.guild, "👤 Membro criado via Discord", f"**Nome:** {payload['nome']}\n**Cargo:** {payload['cargo']}\n**Por:** {interaction.user.mention}", discord.Color.green())

class AddMemberImageButton(discord.ui.Button):
    def __init__(self, member_id):
        super().__init__(label="Enviar Imagem", style=discord.ButtonStyle.secondary)
        self.member_id = member_id
    async def callback(self, interaction):
        await interaction.response.send_message("Envie a imagem como anexo na próxima mensagem (60s).", ephemeral=True)
        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and m.attachments
        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            url = msg.attachments[0].url
            sb = require_supabase()
            if sb:
                sb.table("member_cards").update({"imagem_url": url}).eq("id", self.member_id).execute()
                await msg.reply("Imagem adicionada ao membro!", mention_author=False)
        except Exception:
            await interaction.followup.send("Tempo esgotado ou erro ao capturar imagem.", ephemeral=True)

class AddMemberImageView(discord.ui.View):
    def __init__(self, member_id):
        super().__init__(timeout=120)
        self.add_item(AddMemberImageButton(member_id))

class OpenMemberModal1Button(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Iniciar Cadastro", style=discord.ButtonStyle.primary)
    async def callback(self, interaction):
        await interaction.response.send_modal(MemberModal1())

class MemberStep1View(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(OpenMemberModal1Button())

class OpenMemberModal2Button(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Continuar (2/3)", style=discord.ButtonStyle.primary)
    async def callback(self, interaction):
        await interaction.response.send_modal(MemberModal2())

class MemberStep2View(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(OpenMemberModal2Button())

class OpenMemberModal3Button(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Finalizar (3/3)", style=discord.ButtonStyle.primary)
    async def callback(self, interaction):
        await interaction.response.send_modal(MemberModal3())

class MemberStep3View(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(OpenMemberModal3Button())


# =========================
# EDITAR MEMBRO (3 MODAIS)
# =========================
class MemberEditModal1(discord.ui.Modal, title="Editar 1/3 - Dados Básicos"):
    nome_input = discord.ui.TextInput(label="Nome", max_length=50, required=True)
    idade_input = discord.ui.TextInput(label="Idade", max_length=3, required=False)
    cargo_input = discord.ui.TextInput(
        label="Cargo (membro/veterano/vice_lider/lider)",
        placeholder="membro",
        max_length=20,
        required=False,
        default="membro",
    )
    meta_input = discord.ui.TextInput(label="Meta / subtítulo", max_length=100, required=False)
    personalidade_input = discord.ui.TextInput(
        label="Personalidade",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=False,
    )

    def __init__(self, owner_user_id: int):
        super().__init__()
        self.owner_user_id = int(owner_user_id)
        session = _member_edit_sessions.get(self.owner_user_id, {})
        self.nome_input.default = str(session.get("nome") or "")[:50]
        self.idade_input.default = str(session.get("idade") or "")[:3]
        self.cargo_input.default = str(session.get("cargo") or "membro")[:20]
        self.meta_input.default = str(session.get("meta") or "")[:100]
        self.personalidade_input.default = str(session.get("personalidade") or "")[:500]

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("Esse fluxo não pertence a você.", ephemeral=True)
            return

        session = _member_edit_sessions.get(self.owner_user_id)
        if not session:
            await interaction.response.send_message("Sessão de edição expirada.", ephemeral=True)
            return

        session.update(
            {
                "nome": str(self.nome_input).strip(),
                "idade": str(self.idade_input).strip() or None,
                "cargo": str(self.cargo_input).strip().lower() or "membro",
                "meta": str(self.meta_input).strip() or None,
                "personalidade": str(self.personalidade_input).strip() or None,
            }
        )
        _member_edit_sessions[self.owner_user_id] = session
        await interaction.response.send_message(
            "Etapa 1/3 salva. Continue para a etapa 2.",
            ephemeral=True,
            view=MemberEditStep2View(self.owner_user_id),
        )


class MemberEditModal2(discord.ui.Modal, title="Editar 2/3 - Detalhes"):
    habitos_input = discord.ui.TextInput(label="Hábitos", style=discord.TextStyle.paragraph, max_length=500, required=False)
    gostos_input = discord.ui.TextInput(label="Gostos", style=discord.TextStyle.paragraph, max_length=500, required=False)
    hobbies_input = discord.ui.TextInput(label="Hobbies", max_length=200, required=False)
    tags_input = discord.ui.TextInput(label="Tags (separadas por |)", placeholder="frio | misterioso | elegante", max_length=200, required=False)
    stats_input = discord.ui.TextInput(label="Stats (Label:Valor | Label:Valor)", placeholder="Influência:10 | Presença:9", max_length=300, required=False)

    def __init__(self, owner_user_id: int):
        super().__init__()
        self.owner_user_id = int(owner_user_id)
        session = _member_edit_sessions.get(self.owner_user_id, {})
        self.habitos_input.default = str(session.get("habitos") or "")[:500]
        self.gostos_input.default = str(session.get("gostos") or "")[:500]
        self.hobbies_input.default = str(session.get("hobbies") or "")[:200]
        self.tags_input.default = str(session.get("tags") or "")[:200]
        self.stats_input.default = str(session.get("stats") or "")[:300]

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("Esse fluxo não pertence a você.", ephemeral=True)
            return

        session = _member_edit_sessions.get(self.owner_user_id)
        if not session:
            await interaction.response.send_message("Sessão de edição expirada.", ephemeral=True)
            return

        session.update(
            {
                "habitos": str(self.habitos_input).strip() or None,
                "gostos": str(self.gostos_input).strip() or None,
                "hobbies": str(self.hobbies_input).strip() or None,
                "tags": str(self.tags_input).strip() or None,
                "stats": str(self.stats_input).strip() or None,
            }
        )
        _member_edit_sessions[self.owner_user_id] = session
        await interaction.response.send_message(
            "Etapa 2/3 salva. Finalize na etapa 3.",
            ephemeral=True,
            view=MemberEditStep3View(self.owner_user_id),
        )


class MemberEditModal3(discord.ui.Modal, title="Editar 3/3 - Visual"):
    sigil_input = discord.ui.TextInput(label="Sigil (símbolo)", placeholder="✦", max_length=5, required=False, default="✦")
    imagem_input = discord.ui.TextInput(label="URL da imagem (ou vazio)", max_length=500, required=False)
    cor_input = discord.ui.TextInput(label="Cor (hex ou: purple, gold, pink, cyan)", placeholder="#7c3aed", max_length=20, required=False, default="#7c3aed")
    ordem_input = discord.ui.TextInput(label="Ordem de exibição", placeholder="0", max_length=5, required=False, default="0")

    def __init__(self, owner_user_id: int):
        super().__init__()
        self.owner_user_id = int(owner_user_id)
        session = _member_edit_sessions.get(self.owner_user_id, {})
        self.sigil_input.default = str(session.get("sigil") or "✦")[:5]
        self.imagem_input.default = str(session.get("imagem_url") or "")[:500]
        self.cor_input.default = str(session.get("accent_color") or "#7c3aed")[:20]
        self.ordem_input.default = str(session.get("ordem") or "0")[:5]

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("Esse fluxo não pertence a você.", ephemeral=True)
            return

        session = _member_edit_sessions.get(self.owner_user_id)
        if not session:
            await interaction.response.send_message("Sessão de edição expirada.", ephemeral=True)
            return

        session["sigil"] = str(self.sigil_input).strip() or "✦"
        session["imagem_url"] = str(self.imagem_input).strip() or None
        session["accent_color"] = str(self.cor_input).strip() or "#7c3aed"
        try:
            session["ordem"] = int(str(self.ordem_input).strip() or "0")
        except Exception:
            session["ordem"] = 0

        member_id = session.get("member_id")
        if not member_id:
            await interaction.response.send_message("Sessão inválida.", ephemeral=True)
            _member_edit_sessions.pop(self.owner_user_id, None)
            return

        payload = normalize_member_payload_for_save(session)
        sb = require_supabase()
        if not sb:
            await interaction.response.send_message("Supabase não configurado.", ephemeral=True)
            _member_edit_sessions.pop(self.owner_user_id, None)
            return

        try:
            sb.table("member_cards").update(payload).eq("id", member_id).execute()
        except Exception as e:
            await interaction.response.send_message(f"Erro ao salvar edição: {e}", ephemeral=True)
            _member_edit_sessions.pop(self.owner_user_id, None)
            return

        _member_edit_sessions.pop(self.owner_user_id, None)
        embed = discord.Embed(title="Membro atualizado com sucesso!", color=discord.Color.green())
        embed.add_field(name="ID", value=str(member_id), inline=True)
        embed.add_field(name="Nome", value=payload["nome"], inline=True)
        embed.add_field(name="Cargo", value=payload["cargo"], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

        if interaction.guild:
            await send_log_embed(
                interaction.guild,
                "✏️ Membro editado via Discord",
                f"**ID:** {member_id}\n**Nome:** {payload['nome']}\n**Por:** {interaction.user.mention}",
                discord.Color.blurple(),
            )


class OpenMemberEditModal1Button(discord.ui.Button):
    def __init__(self, owner_user_id: int):
        super().__init__(label="Iniciar Edição (1/3)", style=discord.ButtonStyle.primary)
        self.owner_user_id = int(owner_user_id)

    async def callback(self, interaction):
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("Esse botão não pertence a você.", ephemeral=True)
            return
        await interaction.response.send_modal(MemberEditModal1(self.owner_user_id))


class MemberEditStep1View(discord.ui.View):
    def __init__(self, owner_user_id: int):
        super().__init__(timeout=300)
        self.add_item(OpenMemberEditModal1Button(owner_user_id))


class OpenMemberEditModal2Button(discord.ui.Button):
    def __init__(self, owner_user_id: int):
        super().__init__(label="Continuar (2/3)", style=discord.ButtonStyle.primary)
        self.owner_user_id = int(owner_user_id)

    async def callback(self, interaction):
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("Esse botão não pertence a você.", ephemeral=True)
            return
        await interaction.response.send_modal(MemberEditModal2(self.owner_user_id))


class MemberEditStep2View(discord.ui.View):
    def __init__(self, owner_user_id: int):
        super().__init__(timeout=300)
        self.add_item(OpenMemberEditModal2Button(owner_user_id))


class OpenMemberEditModal3Button(discord.ui.Button):
    def __init__(self, owner_user_id: int):
        super().__init__(label="Finalizar (3/3)", style=discord.ButtonStyle.success)
        self.owner_user_id = int(owner_user_id)

    async def callback(self, interaction):
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("Esse botão não pertence a você.", ephemeral=True)
            return
        await interaction.response.send_modal(MemberEditModal3(self.owner_user_id))


class MemberEditStep3View(discord.ui.View):
    def __init__(self, owner_user_id: int):
        super().__init__(timeout=300)
        self.add_item(OpenMemberEditModal3Button(owner_user_id))



# =========================
# FORMULÁRIO DE MEMBRO (USUÁRIOS) - 3 ETAPAS
# =========================
class MemberFormModal1(discord.ui.Modal, title="1️⃣ Dados Básicos"):
    nome = discord.ui.TextInput(label="Nome do personagem", max_length=50, required=True)
    idade = discord.ui.TextInput(label="Idade", max_length=3, required=False)
    cargo = discord.ui.TextInput(label="Cargo", placeholder="membro", max_length=20, required=False, default="membro")
    meta = discord.ui.TextInput(label="Meta / Subtítulo", max_length=100, required=False)
    personalidade = discord.ui.TextInput(label="Personalidade", style=discord.TextStyle.paragraph, max_length=500, required=False)

    async def on_submit(self, interaction):
        _member_sessions[interaction.user.id] = {
            "nome": str(self.nome).strip(),
            "idade": str(self.idade).strip() or None,
            "cargo": str(self.cargo).strip().lower() or "membro",
            "meta": str(self.meta).strip() or None,
            "personalidade": str(self.personalidade).strip() or None,
        }
        await interaction.response.send_message("Etapa 1 salva! Continue:", view=MemberFormView2())


class MemberFormModal2(discord.ui.Modal, title="2️⃣ Detalhes"):
    habitos = discord.ui.TextInput(label="Hábitos", style=discord.TextStyle.paragraph, max_length=500, required=False)
    gostos = discord.ui.TextInput(label="Gostos", style=discord.TextStyle.paragraph, max_length=500, required=False)
    hobbies = discord.ui.TextInput(label="Hobbies", max_length=200, required=False)
    tags = discord.ui.TextInput(label="Tags (separadas por |)", placeholder="frio | elegante", max_length=200, required=False)
    stats = discord.ui.TextInput(label="Estatísticas", placeholder="Força: 8 | Inteligência: 9 | Carisma: 7", max_length=200, required=False)

    async def on_submit(self, interaction):
        session = _member_sessions.get(interaction.user.id, {})
        session.update({
            "habitos": str(self.habitos).strip() or None,
            "gostos": str(self.gostos).strip() or None,
            "hobbies": str(self.hobbies).strip() or None,
            "tags": str(self.tags).strip() or None,
            "stats": str(self.stats).strip() or None,
        })
        _member_sessions[interaction.user.id] = session
        await interaction.response.send_message("Etapa 2 salva! Última etapa:", view=MemberFormView3(), ephemeral=True)


class MemberFormModal3(discord.ui.Modal, title="3️⃣ Visual"):
    sigil = discord.ui.TextInput(label="Sigil (símbolo)", placeholder="✦", max_length=5, required=False, default="✦")
    cor = discord.ui.TextInput(label="Cor (hex)", placeholder="#7c3aed", max_length=20, required=False, default="#7c3aed")

    async def on_submit(self, interaction):
        session = _member_sessions.get(interaction.user.id, {})
        session.update({
            "sigil": str(self.sigil).strip() or "✦",
            "accent_color": str(self.cor).strip() or "#7c3aed",
            "user_id": interaction.user.id,
            "imagem_url": None,
            "ordem": 0,
            "galeria": [],
            "created_at": datetime.now().timestamp(),
        })
        
        pending = get_pending_member_requests()
        
        if str(interaction.user.id) in pending:
            await interaction.response.send_message("Você já tem um formulário pendente.", ephemeral=True)
            return
        
        if not is_registered_member(interaction.user):
            await interaction.response.send_message("Você precisa estar registrado no servidor para enviar.", ephemeral=True)
            return
        
        pending[str(interaction.user.id)] = session
        persist_storage()
        _member_sessions.pop(interaction.user.id, None)
        
        # Apaga as mensagens anteriores do formulário e envia confirmação
        await interaction.response.send_message("✅ Formulário enviado! A staff vai avaliar.", ephemeral=True)
        
        # Tenta apagar as mensagens do formulário do canal
        try:
            channel = interaction.channel
            async for message in channel.history(limit=20):
                if message.author == bot.user and message.id != interaction.message.id:
                    # Verifica se é mensagem do formulário (tem o botão de iniciar)
                    if message.components and any(
                        hasattr(comp, 'label') and comp.label and "Iniciar" in comp.label 
                        for comp in (message.components[0].children if message.components else [])
                    ):
                        continue  # Não apaga a mensagem inicial com o botão
                    # Apaga outras mensagens do bot (confirmações de etapas)
                    try:
                        await message.delete()
                    except:
                        pass
        except:
            pass
        
        embed = discord.Embed(title="📝 Novo formulário de membro", color=get_embed_color())
        embed.add_field(name="Usuário", value=interaction.user.mention, inline=False)
        embed.add_field(name="Nome", value=session.get("nome"), inline=True)
        embed.add_field(name="Cargo", value=session.get("cargo"), inline=True)
        embed.add_field(name="Idade", value=session.get("idade") or "—", inline=True)
        embed.add_field(name="Meta", value=session.get("meta") or "—", inline=False)
        embed.add_field(name="Personalidade", value=session.get("personalidade") or "—", inline=False)
        embed.add_field(name="Hábitos", value=session.get("habitos") or "—", inline=False)
        embed.add_field(name="Gostos", value=session.get("gostos") or "—", inline=False)
        embed.add_field(name="Hobbies", value=session.get("hobbies") or "—", inline=True)
        embed.add_field(name="Tags", value=session.get("tags") or "—", inline=True)
        embed.add_field(name="Stats", value=session.get("stats") or "—", inline=True)
        embed.add_field(name="Sigil", value=session.get("sigil") or "✦", inline=True)
        embed.add_field(name="Cor", value=session.get("accent_color") or "#7c3aed", inline=True)
        
        approv_channel = interaction.guild.get_channel(config.get("approval_channel_id"))
        if approv_channel:
            await approv_channel.send(embed=embed, view=MemberApprovalView(interaction.user.id))


class OpenMemberFormButton1(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Iniciar (1/3)", style=discord.ButtonStyle.primary)
    
    async def callback(self, interaction):
        await interaction.response.send_modal(MemberFormModal1())


class MemberFormView1(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(OpenMemberFormButton1())


class OpenMemberFormButton2(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Continuar (2/3)", style=discord.ButtonStyle.primary)
    
    async def callback(self, interaction):
        await interaction.response.send_modal(MemberFormModal2())


class MemberFormView2(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(OpenMemberFormButton2())


class OpenMemberFormButton3(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Finalizar (3/3)", style=discord.ButtonStyle.primary)
    
    async def callback(self, interaction):
        await interaction.response.send_modal(MemberFormModal3())


class MemberFormView3(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(OpenMemberFormButton3())


class MemberApprovalButton(discord.ui.Button):
    def __init__(self, user_id, approve=True):
        style = discord.ButtonStyle.success if approve else discord.ButtonStyle.danger
        label = "Aprovar" if approve else "Rejeitar"
        super().__init__(label=label, style=style, custom_id=f"member_approval_{user_id}_{approve}")
        self.user_id = user_id
        self.approve = approve

    async def callback(self, interaction):
        if not has_approver_role(interaction.user):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        
        pending = get_pending_member_requests()
        data = pending.get(str(self.user_id))
        
        if not data:
            await interaction.response.send_message("Formulário não encontrado.", ephemeral=True)
            return
        
        if self.approve:
            sb = require_supabase()
            if sb:
                try:
                    ordem = int(str(data.get("ordem", 0)))
                except (ValueError, TypeError):
                    ordem = 0
                
                idade_val = data.get("idade")
                if idade_val:
                    try:
                        idade_val = int(str(idade_val))
                    except (ValueError, TypeError):
                        idade_val = None
                
                payload = {
                    "nome": data.get("nome"),
                    "idade": idade_val,
                    "cargo": data.get("cargo"),
                    "meta": data.get("meta"),
                    "personalidade": data.get("personalidade"),
                    "habitos": data.get("habitos"),
                    "gostos": data.get("gostos"),
                    "hobbies": data.get("hobbies"),
                    "tags": data.get("tags"),
                    "sigil": data.get("sigil", "✦"),
                    "accent_color": data.get("accent_color", "#7c3aed"),
                    "stats": data.get("stats"),
                    "imagem_url": data.get("imagem_url"),
                    "ordem": ordem,
                    "galeria": data.get("galeria", []),
                }
                if _sb_admin_uuid:
                    payload["criado_por"] = _sb_admin_uuid
                
                try:
                    result = sb.table("member_cards").insert(payload).execute()
                    member_id = result.data[0]["id"] if result.data else None
                except Exception as e:
                    await interaction.response.send_message(f"Erro ao criar: {e}", ephemeral=True)
                    return
                
                pending.pop(str(self.user_id), None)
                persist_storage()
                
                view = AddFormMemberImageView(member_id) if member_id else None
                await interaction.response.send_message(f"✅ Membro `{data['nome']}` criado! ID: {member_id}", ephemeral=True, view=view)
                
                if interaction.guild:
                    user = interaction.guild.get_member(self.user_id)
                    await send_log_embed(interaction.guild, "👤 Membro aprovado via formulário", f"**Nome:** {data['nome']}\n**Por:** {interaction.user.mention}\n**Usuário:** {user.mention if user else 'desconhecido'}", discord.Color.green())
        else:
            pending.pop(str(self.user_id), None)
            persist_storage()
            await interaction.response.send_message("❌ Formulário rejeitado.", ephemeral=True)


class MemberApprovalView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.add_item(MemberApprovalButton(user_id, True))
        self.add_item(MemberApprovalButton(user_id, False))


class AddFormMemberImageButton(discord.ui.Button):
    def __init__(self, member_id):
        super().__init__(label="Adicionar Imagem", style=discord.ButtonStyle.secondary)
        self.member_id = member_id
    
    async def callback(self, interaction):
        await interaction.response.send_message("Envie a imagem como anexo na próxima mensagem (60s).", ephemeral=True)
        
        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and m.attachments
        
        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            url = msg.attachments[0].url
            sb = require_supabase()
            if sb:
                sb.table("member_cards").update({"imagem_url": url}).eq("id", self.member_id).execute()
                await msg.reply("Imagem adicionada!", mention_author=False)
        except Exception:
            await interaction.followup.send("Tempo esgotado.", ephemeral=True)


class AddFormMemberImageView(discord.ui.View):
    def __init__(self, member_id):
        super().__init__(timeout=120)
        self.add_item(AddFormMemberImageButton(member_id))


@bot.command(name="formulariomembro")
async def formulario_membro(ctx):
    cleanup_expired_member_requests()
    embed = discord.Embed(
        title="📝 Formulário de Membro",
        description="Preencha o formulário para solicitar seu card no site.",
        color=get_embed_color()
    )
    await ctx.send(embed=embed, view=MemberFormView1())


@bot.command(name="pendentesmembro")
@commands.cooldown(1, 10, commands.BucketType.user)
async def pendentes_membro(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    
    pending = get_pending_member_requests()
    
    if not pending:
        await ctx.send("Nenhum formulário pendente.")
        return
    
    lines = []
    for uid, data in pending.items():
        lines.append(f"**{data['nome']}** - <@{uid}>")
    
    embed = discord.Embed(title="📝 Formulários pendentes", description="\n".join(lines), color=get_embed_color())
    await ctx.send(embed=embed)


@bot.command(name="criarmembro")
@commands.cooldown(1, 10, commands.BucketType.user)
async def criar_membro(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão para criar membros.")
        return
    if not require_supabase():
        await ctx.send("Supabase não configurado.")
        return
    _member_sessions.pop(ctx.author.id, None)
    await ctx.send("Clique para iniciar o cadastro do membro (3 etapas):", view=MemberStep1View())

# =========================
# COMANDOS AUXILIARES
# =========================
@bot.command(name="listareventos")
@commands.cooldown(1, 10, commands.BucketType.user)
async def listar_eventos(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        result = sb.table("events").select("id, titulo, data_evento, horario, local").order("data_evento", desc=True).limit(10).execute()
        eventos = result.data or []
    except Exception as e:
        await ctx.send(f"Erro: {e}")
        return
    if not eventos:
        await ctx.send("Nenhum evento encontrado.")
        return
    lines = [f"**#{ev['id']}** — {ev['titulo']} | {ev['data_evento']} | {ev.get('horario') or '—'} | {ev.get('local') or '—'}" for ev in eventos]
    embed = discord.Embed(title="📅 Últimos eventos", description="\n".join(lines), color=get_embed_color())
    await ctx.send(embed=embed)

@bot.command(name="rankfrat", aliases=["rankingfrat", "listarankingfrat"])
@commands.cooldown(1, 8, commands.BucketType.user)
async def rank_frat(ctx):
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        result = (
            sb.table("fraternity_rankings")
            .select("id, nome, pontos, foguete_emoji, ativo")
            .eq("ativo", True)
            .order("pontos", desc=True)
            .order("id", desc=False)
            .limit(25)
            .execute()
        )
        items = result.data or []
    except Exception as e:
        await ctx.send(f"Erro ao carregar ranking: {e}")
        return

    if not items:
        await ctx.send("Nenhuma fraternidade no ranking ainda.")
        return

    lines = []
    for idx, item in enumerate(items, start=1):
        emoji = str(item.get("foguete_emoji") or "🚀")
        lines.append(f"`#{idx}` {emoji} **{item.get('nome', 'Sem nome')}** — {int(item.get('pontos') or 0)} pts (ID {item.get('id')})")

    embed = discord.Embed(
        title="🚀 Ranking das Fraternidades",
        description="\n".join(lines),
        color=get_embed_color(),
    )
    await ctx.send(embed=embed)

@bot.command(name="addfraternidade")
@commands.cooldown(1, 8, commands.BucketType.user)
async def add_fraternidade(ctx, *, dados: str = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    if not dados:
        await ctx.send("Uso: `!addfraternidade <nome> | <pontos_opcional>`")
        return

    partes = [p.strip() for p in dados.split("|", 1)]
    nome = partes[0] if partes else ""
    pontos = 0
    if len(partes) > 1 and partes[1]:
        try:
            pontos = int(partes[1])
        except Exception:
            await ctx.send("Pontos inválidos. Exemplo: `!addfraternidade Eclipse | 120`")
            return

    if not nome:
        await ctx.send("Informe o nome da fraternidade.")
        return

    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        created = (
            sb.table("fraternity_rankings")
            .insert(
                {
                    "nome": nome,
                    "pontos": pontos,
                    "cor": "#a855f7",
                    "foguete_emoji": "🚀",
                    "ativo": True,
                }
            )
            .select("id, nome, pontos")
            .single()
            .execute()
        )
        item = created.data or {}
        await ctx.send(
            f"✅ Fraternidade criada: **{item.get('nome', nome)}** (ID `{item.get('id')}`) com **{item.get('pontos', pontos)} pts**."
        )
    except Exception as e:
        await ctx.send(f"Erro ao criar fraternidade: {e}")

@bot.command(name="removerfraternidade")
@commands.cooldown(1, 8, commands.BucketType.user)
async def remover_fraternidade(ctx, ranking_id: int = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    if not ranking_id:
        await ctx.send("Uso: `!removerfraternidade <id>`")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        sb.table("fraternity_rankings").delete().eq("id", ranking_id).execute()
        await ctx.send(f"🗑️ Fraternidade de ID `{ranking_id}` removida.")
    except Exception as e:
        await ctx.send(f"Erro ao remover fraternidade: {e}")

@bot.command(name="somarpontosfrat", aliases=["addpontosfrat"])
@commands.cooldown(1, 6, commands.BucketType.user)
async def somar_pontos_frat(ctx, ranking_id: int = None, pontos: int = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    if ranking_id is None or pontos is None:
        await ctx.send("Uso: `!somarpontosfrat <id> <valor>`")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        current_res = (
            sb.table("fraternity_rankings")
            .select("id, nome, pontos")
            .eq("id", ranking_id)
            .maybe_single()
            .execute()
        )
        item = current_res.data
        if not item:
            await ctx.send("Fraternidade não encontrada.")
            return
        novo_total = int(item.get("pontos") or 0) + int(pontos)
        updated = (
            sb.table("fraternity_rankings")
            .update({"pontos": novo_total})
            .eq("id", ranking_id)
            .select("id, nome, pontos")
            .single()
            .execute()
        )
        up = updated.data or {}
        await ctx.send(
            f"✅ Pontos atualizados: **{up.get('nome', item.get('nome'))}** agora está com **{up.get('pontos', novo_total)} pts**."
        )
    except Exception as e:
        await ctx.send(f"Erro ao alterar pontos: {e}")

@bot.command(name="definirpontosfrat")
@commands.cooldown(1, 6, commands.BucketType.user)
async def definir_pontos_frat(ctx, ranking_id: int = None, pontos: int = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    if ranking_id is None or pontos is None:
        await ctx.send("Uso: `!definirpontosfrat <id> <valor>`")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        updated = (
            sb.table("fraternity_rankings")
            .update({"pontos": int(pontos)})
            .eq("id", ranking_id)
            .select("id, nome, pontos")
            .maybe_single()
            .execute()
        )
        item = updated.data
        if not item:
            await ctx.send("Fraternidade não encontrada.")
            return
        await ctx.send(
            f"✅ Pontuação definida: **{item.get('nome')}** agora está com **{item.get('pontos')} pts**."
        )
    except Exception as e:
        await ctx.send(f"Erro ao definir pontos: {e}")

@bot.command(name="editarfraternidade", aliases=["editarfrat"])
@commands.cooldown(1, 6, commands.BucketType.user)
async def editar_fraternidade(ctx, ranking_id: int = None, campo: str = None, *, valor: str = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    if ranking_id is None or not campo or valor is None:
        await ctx.send(
            "Uso: `!editarfraternidade <id> <campo> <valor>`\n"
            "Campos: `nome`, `foguete` (ou `emoji`), `cor`, `pontos`."
        )
        return

    campo_norm = str(campo).strip().lower()
    update_payload = {}

    if campo_norm == "nome":
        novo_nome = str(valor).strip()
        if not novo_nome:
            await ctx.send("Nome inválido.")
            return
        update_payload["nome"] = novo_nome
    elif campo_norm in ("foguete", "emoji", "foguete_emoji"):
        novo_emoji = str(valor).strip()
        if not novo_emoji:
            await ctx.send("Emoji do foguete inválido.")
            return
        update_payload["foguete_emoji"] = novo_emoji
    elif campo_norm == "cor":
        nova_cor = str(valor).strip()
        if not nova_cor:
            await ctx.send("Cor inválida. Exemplo: `#a855f7`.")
            return
        update_payload["cor"] = nova_cor
    elif campo_norm == "pontos":
        try:
            update_payload["pontos"] = int(valor)
        except Exception:
            await ctx.send("Pontos inválidos. Use um número inteiro.")
            return
    else:
        await ctx.send("Campo inválido. Use: `nome`, `foguete`, `cor`, `pontos`.")
        return

    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        updated = (
            sb.table("fraternity_rankings")
            .update(update_payload)
            .eq("id", ranking_id)
            .select("id, nome, pontos, foguete_emoji, cor")
            .maybe_single()
            .execute()
        )
        item = updated.data
        if not item:
            await ctx.send("Fraternidade não encontrada.")
            return
        await ctx.send(
            f"✅ Fraternidade atualizada (ID `{item.get('id')}`): "
            f"{item.get('foguete_emoji') or '🚀'} **{item.get('nome')}** — "
            f"{item.get('pontos')} pts | cor {item.get('cor')}"
        )
    except Exception as e:
        await ctx.send(f"Erro ao editar fraternidade: {e}")

@bot.command(name="listarmembros")
@commands.cooldown(1, 10, commands.BucketType.user)
async def listar_membros(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        result = sb.table("member_cards").select("id, nome, cargo, ordem").order("ordem", desc=False).execute()
        membros = result.data or []
    except Exception as e:
        await ctx.send(f"Erro: {e}")
        return
    if not membros:
        await ctx.send("Nenhum membro encontrado.")
        return
    lines = [f"**#{m['id']}** — {m['nome']} | {m['cargo']} | ordem: {m.get('ordem', 0)}" for m in membros[:25]]
    embed = discord.Embed(title="👤 Membros cadastrados", description="\n".join(lines), color=get_embed_color())
    await ctx.send(embed=embed)

@bot.command(name="deletarevento")
@commands.cooldown(1, 10, commands.BucketType.user)
async def deletar_evento(ctx, event_id: int = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    if not event_id:
        await ctx.send("Uso: `!deletarevento <id>`")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        sb.table("events").delete().eq("id", event_id).execute()
        await ctx.send(f"Evento #{event_id} excluído com sucesso.")
        if ctx.guild:
            await send_log_embed(ctx.guild, "🗑️ Evento excluído via Discord", f"**ID:** {event_id}\n**Por:** {ctx.author.mention}", discord.Color.red())
    except Exception as e:
        await ctx.send(f"Erro: {e}")

@bot.command(name="deletarmembro")
@commands.cooldown(1, 10, commands.BucketType.user)
async def deletar_membro(ctx, member_id: int = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    if not member_id:
        await ctx.send("Uso: `!deletarmembro <id>`")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        sb.table("member_cards").delete().eq("id", member_id).execute()
        await ctx.send(f"Membro #{member_id} excluído com sucesso.")
        if ctx.guild:
            await send_log_embed(ctx.guild, "🗑️ Membro excluído via Discord", f"**ID:** {member_id}\n**Por:** {ctx.author.mention}", discord.Color.red())
    except Exception as e:
        await ctx.send(f"Erro: {e}")

# =========================
# FIM DO CODIGO SUPABASE
# =========================

# =========================
# PARCERIAS - COLE NO bot.py ANTES DO TOKEN
# =========================

class CriarParceriaModal(discord.ui.Modal, title="Criar Parceria"):
    nome_input = discord.ui.TextInput(label="Nome da empresa", max_length=100, required=True)
    descricao_input = discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, max_length=500, required=False)
    beneficios_input = discord.ui.TextInput(label="Benefícios (1 por linha)", style=discord.TextStyle.paragraph, max_length=500, required=False)
    discord_input = discord.ui.TextInput(label="Link do Discord", placeholder="https://discord.gg/...", max_length=200, required=False)
    codigo_input = discord.ui.TextInput(label="Código de desconto", max_length=100, required=False)

    def __init__(self, defaults=None, editing_id=None):
        super().__init__()
        self.editing_id = editing_id
        if defaults:
            self.nome_input.default = defaults.get("nome", "")[:100]
            self.descricao_input.default = defaults.get("descricao", "")[:500]
            self.beneficios_input.default = defaults.get("beneficios", "")[:500]
            self.discord_input.default = defaults.get("discord_link", "")[:200]
            self.codigo_input.default = defaults.get("codigo_desconto", "")[:100]

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        
        sb = require_supabase()
        if not sb:
            await interaction.followup.send("Supabase não configurado.", ephemeral=True)
            return

        payload = {
            "nome": str(self.nome_input).strip(),
            "descricao": str(self.descricao_input).strip() or None,
            "beneficios": str(self.beneficios_input).strip() or None,
            "discord_link": str(self.discord_input).strip() or None,
            "codigo_desconto": str(self.codigo_input).strip() or None,
            "cor_destaque": "#7c3aed",
            "ativo": True,
            "ordem": 0,
        }

        try:
            if self.editing_id:
                sb.table("partners").update(payload).eq("id", self.editing_id).execute()
                action = "atualizada"
                partner_id = self.editing_id
            else:
                result = sb.table("partners").insert(payload).execute()
                partner_id = result.data[0]["id"] if result.data else None
                action = "criada"
        except Exception as e:
            await interaction.followup.send(f"Erro: {e}", ephemeral=True)
            return

        embed = discord.Embed(title=f"Parceria {action} com sucesso!", color=discord.Color.green())
        embed.add_field(name="Nome", value=payload["nome"], inline=True)
        if payload["discord_link"]:
            embed.add_field(name="Discord", value=payload["discord_link"], inline=True)
        if payload["codigo_desconto"]:
            embed.add_field(name="Código", value=payload["codigo_desconto"], inline=True)

        view = AddPartnerLogoView(partner_id) if partner_id else None
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        if interaction.guild:
            await send_log_embed(interaction.guild, f"🤝 Parceria {action} via Discord", f"**Nome:** {payload['nome']}\n**Por:** {interaction.user.mention}", discord.Color.green())


class AddPartnerLogoButton(discord.ui.Button):
    def __init__(self, partner_id):
        super().__init__(label="Adicionar Logo", style=discord.ButtonStyle.secondary)
        self.partner_id = partner_id

    async def callback(self, interaction):
        await interaction.response.send_message("Envie o logo como anexo na próxima mensagem (60s).", ephemeral=True)

        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and m.attachments

        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            url = msg.attachments[0].url
            sb = require_supabase()
            if sb:
                sb.table("partners").update({"logo_url": url}).eq("id", self.partner_id).execute()
                await msg.reply("Logo adicionado à parceria!", mention_author=False)
        except Exception:
            await interaction.followup.send("Tempo esgotado ou erro ao capturar imagem.", ephemeral=True)


class AddPartnerLogoView(discord.ui.View):
    def __init__(self, partner_id):
        super().__init__(timeout=120)
        self.add_item(AddPartnerLogoButton(partner_id))


class OpenCriarParceriaButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Criar Parceria", style=discord.ButtonStyle.primary)

    async def callback(self, interaction):
        await interaction.response.send_modal(CriarParceriaModal())


class CriarParceriaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(OpenCriarParceriaButton())


@bot.command(name="criarparceria")
@commands.cooldown(1, 10, commands.BucketType.user)
async def criar_parceria(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão para criar parcerias.")
        return
    if not require_supabase():
        await ctx.send("Supabase não configurado.")
        return
    await ctx.send("Clique no botão para criar uma parceria:", view=CriarParceriaView())


class EditPartnerSelect(discord.ui.Select):
    def __init__(self, partners):
        options = [
            discord.SelectOption(label=f"{p['nome']} (#{p['id']})", value=str(p["id"]))
            for p in partners[:25]
        ]
        super().__init__(placeholder="Selecione a parceria para editar", options=options)
        self.partners_data = {str(p["id"]): p for p in partners[:25]}

    async def callback(self, interaction):
        data = self.partners_data.get(self.values[0], {})
        editing_id = int(self.values[0])
        await interaction.response.send_modal(CriarParceriaModal(defaults=data, editing_id=editing_id))


class EditPartnerView(discord.ui.View):
    def __init__(self, partners):
        super().__init__(timeout=120)
        self.add_item(EditPartnerSelect(partners))


@bot.command(name="editarparceria")
@commands.cooldown(1, 10, commands.BucketType.user)
async def editar_parceria(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        result = sb.table("partners").select("*").order("ordem", desc=False).execute()
        partners = result.data or []
    except Exception as e:
        await ctx.send(f"Erro: {e}")
        return
    if not partners:
        await ctx.send("Nenhuma parceria cadastrada.")
        return
    await ctx.send("Selecione a parceria para editar:", view=EditPartnerView(partners))


@bot.command(name="listarparcerias")
@commands.cooldown(1, 10, commands.BucketType.user)
async def listar_parcerias(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        result = sb.table("partners").select("id, nome, ativo, ordem").order("ordem", desc=False).execute()
        parcerias = result.data or []
    except Exception as e:
        await ctx.send(f"Erro: {e}")
        return
    if not parcerias:
        await ctx.send("Nenhuma parceria cadastrada.")
        return
    lines = [f"**#{p['id']}** — {p['nome']} | {'Ativa' if p['ativo'] else 'Inativa'} | ordem: {p.get('ordem', 0)}" for p in parcerias]
    embed = discord.Embed(title="🤝 Parcerias cadastradas", description="\n".join(lines), color=get_embed_color())
    await ctx.send(embed=embed)


@bot.command(name="deletarparceria")
@commands.cooldown(1, 10, commands.BucketType.user)
async def deletar_parceria(ctx, partner_id: int = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    if not partner_id:
        await ctx.send("Uso: `!deletarparceria <id>`")
        return
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    try:
        sb.table("partners").delete().eq("id", partner_id).execute()
        await ctx.send(f"Parceria #{partner_id} excluída com sucesso.")
        if ctx.guild:
            await send_log_embed(ctx.guild, "🗑️ Parceria excluída via Discord", f"**ID:** {partner_id}\n**Por:** {ctx.author.mention}", discord.Color.red())
    except Exception as e:
        await ctx.send(f"Erro: {e}")

# =========================
# COMANDOS DE PARCERIAS PARA O BOT - COLE NO bot.py
# =========================

# ---------- !parcerias ----------
class ParceriasSelect(discord.ui.Select):
    def __init__(self, partners):
        options = [
            discord.SelectOption(
                label=p['nome'][:25],
                description=(p.get('descricao') or 'Parceiro oficial')[:50],
                value=str(p['id'])
            )
            for p in partners[:25]
        ]
        super().__init__(placeholder="Selecione uma parceria", options=options)
        self.partners_data = {str(p['id']): p for p in partners}

    async def callback(self, interaction):
        p = self.partners_data.get(self.values[0], {})
        
        # Verifica se tem link de Discord válido
        discord_link = p.get('discord_link', '')
        tem_discord = discord_link and discord_link.startswith(('http://', 'https://', 'discord://'))
        
        embed = discord.Embed(
            title=f"🤝 {p.get('nome', 'Parceiro')}",
            description=p.get('descricao') or 'Sem descrição',
            color=discord.Color.purple()
        )
        
        if p.get('logo_url'):
            embed.set_thumbnail(url=p['logo_url'])
        
        if p.get('beneficios'):
            beneficios = p['beneficios'][:200] + '...' if len(p['beneficios']) > 200 else p['beneficios']
            embed.add_field(name="Benefícios", value=beneficios, inline=False)
        
        if p.get('codigo_desconto'):
            embed.add_field(name="Código", value=f"`{p['codigo_desconto']}`", inline=True)
        
        # Adiciona campo de Discord (com link ou aviso)
        if tem_discord:
            embed.add_field(
                name="Discord",
                value=f"[Entrar no Discord]({discord_link})",
                inline=True
            )
        else:
            embed.add_field(
                name="Discord",
                value="🔒 *Link não disponível*",
                inline=True
            )
        
        view = discord.ui.View()
        
        # Botão para o site
        site_btn = discord.ui.Button(
            label="Ver no site",
            url=f"https://iconics-jade.vercel.app/parceria/{p['id']}",
            style=discord.ButtonStyle.link
        )
        view.add_item(site_btn)
        
        # Botão para Discord da parceria (só se tiver link válido)
        if tem_discord:
            discord_btn = discord.ui.Button(
                label="Discord da parceria",
                url=discord_link,
                style=discord.ButtonStyle.link
            )
            view.add_item(discord_btn)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ParceriasView(discord.ui.View):
    def __init__(self, partners):
        super().__init__(timeout=120)
        self.add_item(ParceriasSelect(partners))


@bot.command(name="parcerias")
@commands.cooldown(1, 10, commands.BucketType.user)
async def parcerias_cmd(ctx):
    """Mostra as parcerias da Iconics com links para o site"""
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    
    try:
        result = sb.table("partners").select("*").eq("ativo", True).order("ordem", desc=False).execute()
        partners = result.data or []
    except Exception as e:
        await ctx.send(f"Erro ao buscar parcerias: {e}")
        return
    
    if not partners:
        await ctx.send("Nenhuma parceria ativa no momento.")
        return
    
    embed = discord.Embed(
        title="🤝 Parceiros da Iconics",
        description=f"Temos **{len(partners)}** parceiros oficiais!\n\nSelecione uma parceria abaixo para ver mais detalhes e acessar o site.",
        color=discord.Color.purple()
    )
    
    # Lista resumida
    lista = "\n".join([f"• {p['nome']}" for p in partners[:10]])
    if len(partners) > 10:
        lista += f"\n... e mais {len(partners) - 10}"
    embed.add_field(name="Parceiros", value=lista, inline=False)
    
    view = ParceriasView(partners)
    await ctx.send(embed=embed, view=view)


# ---------- !adicionarfotoparceria ----------
class AddFotoParceriaSelect(discord.ui.Select):
    def __init__(self, partners):
        options = [
            discord.SelectOption(label=p['nome'][:25], value=str(p['id']))
            for p in partners[:25]
        ]
        super().__init__(placeholder="Selecione a parceria", options=options)
        self.partners_data = {str(p['id']): p for p in partners}

    async def callback(self, interaction):
        partner_id = int(self.values[0])
        partner = self.partners_data.get(self.values[0], {})
        
        await interaction.response.send_message(
            f"**{partner['nome']}** - Envie as fotos como anexos (máx 10, 60s).",
            ephemeral=True
        )
        
        def check(m):
            return (
                m.author.id == interaction.user.id and 
                m.channel.id == interaction.channel.id and 
                m.attachments
            )
        
        fotos_urls = []
        
        while len(fotos_urls) < 10:
            try:
                msg = await bot.wait_for("message", check=check, timeout=60)
                for att in msg.attachments[:10 - len(fotos_urls)]:
                    fotos_urls.append(att.url)
                
                # Confirmação por mensagem
                await msg.reply(f"✅ {len(msg.attachments)} foto(s) recebida(s)!", mention_author=False)
                
                # Pergunta se quer adicionar mais
                if len(fotos_urls) < 10:
                    confirm_msg = await interaction.followup.send(
                        f"Adicionadas {len(fotos_urls)}/10 fotos. Envie mais ou digite `pronto`.",
                        ephemeral=True
                    )
                    
                    def check_pronto(m):
                        return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
                    
                    try:
                        resposta = await bot.wait_for("message", check=check_pronto, timeout=30)
                        if resposta.content.lower() in ["pronto", "ok", "fim"]:
                            break
                        # Se enviou mais fotos, o loop continua
                    except:
                        break
                
            except:
                break
        
        if not fotos_urls:
            await interaction.followup.send("Nenhuma foto foi enviada.", ephemeral=True)
            return
        
        # Salvar no Supabase
        sb = require_supabase()
        if not sb:
            await interaction.followup.send("Supabase não configurado.", ephemeral=True)
            return
        
        try:
            # Buscar galeria atual
            result = sb.table("partners").select("galeria").eq("id", partner_id).single().execute()
            galeria_atual = result.data.get("galeria") or []
            
            # Adicionar novas fotos
            nova_galeria = galeria_atual + fotos_urls
            
            # Limitar a 20 fotos
            if len(nova_galeria) > 20:
                nova_galeria = nova_galeria[:20]
            
            # Atualizar
            sb.table("partners").update({"galeria": nova_galeria}).eq("id", partner_id).execute()
            
            embed = discord.Embed(
                title="✅ Fotos adicionadas!",
                description=f"{len(fotos_urls)} foto(s) adicionadas à galeria de **{partner['nome']}**.",
                color=discord.Color.green()
            )
            embed.add_field(name="Total na galeria", value=str(len(nova_galeria)))
            embed.add_field(name="Ver no site", value=f"[Clique aqui](https://iconics-jade.vercel.app/parceria/{partner_id})")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # Log
            if interaction.guild:
                await send_log_embed(
                    interaction.guild,
                    "📸 Fotos adicionadas à parceria",
                    f"**Parceiro:** {partner['nome']}\n**Fotos:** {len(fotos_urls)}\n**Por:** {interaction.user.mention}",
                    discord.Color.green()
                )
                
        except Exception as e:
            await interaction.followup.send(f"Erro ao salvar: {e}", ephemeral=True)


class AddFotoParceriaView(discord.ui.View):
    def __init__(self, partners):
        super().__init__(timeout=120)
        self.add_item(AddFotoParceriaSelect(partners))


@bot.command(name="adicionarfotoparceria")
@commands.cooldown(1, 30, commands.BucketType.user)
async def adicionar_foto_parceria(ctx):
    """Adiciona fotos à galeria de uma parceria"""
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    
    try:
        result = sb.table("partners").select("id, nome").order("ordem", desc=False).execute()
        partners = result.data or []
    except Exception as e:
        await ctx.send(f"Erro: {e}")
        return
    
    if not partners:
        await ctx.send("Nenhuma parceria cadastrada.")
        return
    
    await ctx.send("Selecione a parceria para adicionar fotos:", view=AddFotoParceriaView(partners))


# ---------- !vergaleriaparceria ----------
@bot.command(name="vergaleriaparceria")
@commands.cooldown(1, 10, commands.BucketType.user)
async def ver_galeria_parceria(ctx, partner_id: int = None):
    """Mostra a galeria de fotos de uma parceria"""
    if not partner_id:
        await ctx.send("Uso: `!vergaleriaparceria <id>`")
        return
    
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    
    try:
        result = sb.table("partners").select("nome, galeria").eq("id", partner_id).single().execute()
        partner = result.data
    except:
        await ctx.send("Parceria não encontrada.")
        return
    
    galeria = partner.get("galeria") or []
    
    if not galeria:
        await ctx.send(f"**{partner['nome']}** não possui fotos na galeria.")
        return
    
    embed = discord.Embed(
        title=f"📸 Galeria - {partner['nome']}",
        description=f"{len(galeria)} foto(s) na galeria",
        color=discord.Color.purple()
    )
    
    # Mostrar preview das primeiras 4 fotos
    for i, url in enumerate(galeria[:4]):
        embed.add_field(name=f"Foto {i+1}", value=f"[Ver]({url})", inline=True)
    
    if len(galeria) > 4:
        embed.set_footer(text=f"... e mais {len(galeria) - 4} fotos")
    
    embed.add_field(
        name="Ver no site",
        value=f"[Clique aqui](https://iconics-jade.vercel.app/parceria/{partner_id})",
        inline=False
    )
    
    await ctx.send(embed=embed)


# ---------- !removerfotoparceria ----------
@bot.command(name="removerfotoparceria")
@commands.cooldown(1, 10, commands.BucketType.user)
async def remover_foto_parceria(ctx, partner_id: int = None, index: int = None):
    """Remove uma foto da galeria (índice começa em 1)"""
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    
    if not partner_id or index is None:
        await ctx.send("Uso: `!removerfotoparceria <id_parceria> <numero_foto>`\nUse `!vergaleriaparceria <id>` para ver os números.")
        return
    
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    
    try:
        result = sb.table("partners").select("nome, galeria").eq("id", partner_id).single().execute()
        partner = result.data
        galeria = partner.get("galeria") or []
        
        if index < 1 or index > len(galeria):
            await ctx.send(f"Índice inválido. A galeria tem {len(galeria)} fotos.")
            return
        
        # Remover foto (índice-1 porque usuário vê começando em 1)
        foto_removida = galeria.pop(index - 1)
        
        sb.table("partners").update({"galeria": galeria}).eq("id", partner_id).execute()
        
        await ctx.send(f"✅ Foto {index} removida da galeria de **{partner['nome']}**.")
        
        if ctx.guild:
            await send_log_embed(
                ctx.guild,
                "🗑️ Foto removida da parceria",
                f"**Parceiro:** {partner['nome']}\n**Por:** {ctx.author.mention}",
                discord.Color.red()
            )
    except Exception as e:
        await ctx.send(f"Erro: {e}")


# FIM DOS COMANDOS DE PARCERIAS


# =========================
# COMANDOS DE GALERIA DE MEMBROS - COLE NO bot.py
# =========================

class AddFotoMembroSelect(discord.ui.Select):
    def __init__(self, members):
        options = [
            discord.SelectOption(label=f"{m['nome'][:20]} (#{m['id']})", value=str(m['id']))
            for m in members[:25]
        ]
        super().__init__(placeholder="Selecione o membro", options=options)
        self.members_data = {str(m['id']): m for m in members}

    async def callback(self, interaction):
        member_id = int(self.values[0])
        member = self.members_data.get(self.values[0], {})
        
        await interaction.response.send_message(
            f"**{member.get('nome', 'Membro')}** - Envie as fotos como anexos (máx 16, 60s).",
            ephemeral=True
        )
        
        def check(m):
            return (
                m.author.id == interaction.user.id and 
                m.channel.id == interaction.channel.id and 
                m.attachments
            )
        
        fotos_urls = []
        
        while len(fotos_urls) < 16:
            try:
                msg = await bot.wait_for("message", check=check, timeout=60)
                for att in msg.attachments[:16 - len(fotos_urls)]:
                    fotos_urls.append(att.url)
                
                await msg.reply(f"✅ {len(msg.attachments)} foto(s) recebida(s)!", mention_author=False)
                
                if len(fotos_urls) < 16:
                    await interaction.followup.send(
                        f"Adicionadas {len(fotos_urls)}/16 fotos. Envie mais ou digite `pronto`.",
                        ephemeral=True
                    )
                    
                    def check_pronto(m):
                        return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
                    
                    try:
                        resposta = await bot.wait_for("message", check=check_pronto, timeout=30)
                        if resposta.content.lower() in ["pronto", "ok", "fim"]:
                            break
                    except:
                        break
                
            except:
                break
        
        if not fotos_urls:
            await interaction.followup.send("Nenhuma foto foi enviada.", ephemeral=True)
            return
        
        sb = require_supabase()
        if not sb:
            await interaction.followup.send("Supabase não configurado.", ephemeral=True)
            return
        
        try:
            # Buscar galeria atual
            result = sb.table("member_cards").select("galeria, nome").eq("id", member_id).single().execute()
            galeria_atual = result.data.get("galeria") or []
            nome_membro = result.data.get("nome", "Membro")
            
            # Adicionar novas fotos
            nova_galeria = galeria_atual + fotos_urls
            
            # Limitar a 16 fotos
            if len(nova_galeria) > 16:
                nova_galeria = nova_galeria[:16]
            
            # Atualizar
            sb.table("member_cards").update({"galeria": nova_galeria}).eq("id", member_id).execute()
            
            embed = discord.Embed(
                title="✅ Fotos adicionadas!",
                description=f"{len(fotos_urls)} foto(s) adicionadas à galeria de **{nome_membro}**.",
                color=discord.Color.green()
            )
            embed.add_field(name="Total na galeria", value=f"{len(nova_galeria)}/16")
            embed.add_field(name="Ver no site", value=f"[Clique aqui](https://iconics-jade.vercel.app/membro/{member_id})")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            if interaction.guild:
                await send_log_embed(
                    interaction.guild,
                    "📸 Fotos adicionadas ao membro",
                    f"**Membro:** {nome_membro}\n**Fotos:** {len(fotos_urls)}\n**Por:** {interaction.user.mention}",
                    discord.Color.green()
                )
                
        except Exception as e:
            await interaction.followup.send(f"Erro ao salvar: {e}", ephemeral=True)


class AddFotoMembroView(discord.ui.View):
    def __init__(self, members):
        super().__init__(timeout=120)
        self.add_item(AddFotoMembroSelect(members))


@bot.command(name="adicionarfotomembro")
@commands.cooldown(1, 30, commands.BucketType.user)
async def adicionar_foto_membro(ctx):
    """Adiciona fotos à galeria de um membro"""
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    
    try:
        result = sb.table("member_cards").select("id, nome").order("ordem", desc=False).execute()
        members = result.data or []
    except Exception as e:
        await ctx.send(f"Erro: {e}")
        return
    
    if not members:
        await ctx.send("Nenhum membro cadastrado.")
        return
    
    await ctx.send("Selecione o membro para adicionar fotos:", view=AddFotoMembroView(members))


@bot.command(name="vergaleriamembro")
@commands.cooldown(1, 10, commands.BucketType.user)
async def ver_galeria_membro(ctx, member_id: int = None):
    """Mostra a galeria de fotos de um membro"""
    if not member_id:
        await ctx.send("Uso: `!vergaleriamembro <id>`")
        return
    
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    
    try:
        result = sb.table("member_cards").select("nome, galeria").eq("id", member_id).single().execute()
        member = result.data
    except:
        await ctx.send("Membro não encontrado.")
        return
    
    galeria = member.get("galeria") or []
    
    if not galeria:
        await ctx.send(f"**{member['nome']}** não possui fotos na galeria.")
        return
    
    embed = discord.Embed(
        title=f"📸 Galeria - {member['nome']}",
        description=f"{len(galeria)} foto(s) na galeria",
        color=discord.Color.purple()
    )
    
    # Mostrar preview das primeiras 4 fotos
    for i, url in enumerate(galeria[:4]):
        embed.add_field(name=f"Foto {i+1}", value=f"[Ver]({url})", inline=True)
    
    if len(galeria) > 4:
        embed.set_footer(text=f"... e mais {len(galeria) - 4} fotos")
    
    embed.add_field(
        name="Ver no site",
        value=f"[Clique aqui](https://iconics-jade.vercel.app/membro/{member_id})",
        inline=False
    )
    
    await ctx.send(embed=embed)


@bot.command(name="removerfotomembro")
@commands.cooldown(1, 10, commands.BucketType.user)
async def remover_foto_membro(ctx, member_id: int = None, index: int = None):
    """Remove uma foto da galeria de um membro (índice começa em 1)"""
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão.")
        return
    
    if not member_id or index is None:
        await ctx.send("Uso: `!removerfotomembro <id_membro> <numero_foto>`\nUse `!vergaleriamembro <id>` para ver os números.")
        return
    
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return
    
    try:
        result = sb.table("member_cards").select("nome, galeria").eq("id", member_id).single().execute()
        member = result.data
        galeria = member.get("galeria") or []
        
        if index < 1 or index > len(galeria):
            await ctx.send(f"Índice inválido. A galeria tem {len(galeria)} fotos.")
            return
        
        # Remover foto
        foto_removida = galeria.pop(index - 1)
        
        sb.table("member_cards").update({"galeria": galeria}).eq("id", member_id).execute()
        
        await ctx.send(f"✅ Foto {index} removida da galeria de **{member['nome']}**.")
        
        if ctx.guild:
            await send_log_embed(
                ctx.guild,
                "🗑️ Foto removida do membro",
                f"**Membro:** {member['nome']}\n**Por:** {ctx.author.mention}",
                discord.Color.red()
            )
    except Exception as e:
        await ctx.send(f"Erro: {e}")


# FIM DOS COMANDOS DE GALERIA DE MEMBROS


MEMBER_LINK_SECRET = os.getenv("MEMBER_LINK_CODE_SECRET") or config.get("member_link_secret") or "iconics-member-link"


def normalize_member_access_code(value: str) -> str:
    return str(value or "").strip().upper()


def hash_member_access_code(member_id: int, raw_code: str) -> str:
    normalized = normalize_member_access_code(raw_code)
    payload = f"{MEMBER_LINK_SECRET}:{member_id}:{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_member_link_editor_by_discord(discord_user_id: int):
    sb = require_supabase()
    if not sb:
        return None

    try:
        result = (
            sb.table("member_card_links")
            .select("id, member_card_id, can_edit, status")
            .eq("discord_user_id", int(discord_user_id))
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def get_member_card_by_id(member_id: int):
    sb = require_supabase()
    if not sb:
        return None

    try:
        result = (
            sb.table("member_cards")
            .select("id, nome, access_code_hash, galeria")
            .eq("id", int(member_id))
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def get_member_card_for_edit(member_id: int):
    sb = require_supabase()
    if not sb:
        return None

    fields = (
        "id, nome, idade, cargo, meta, personalidade, habitos, gostos, hobbies, "
        "tags, stats, sigil, imagem_url, accent_color, ordem, galeria"
    )
    try:
        result = (
            sb.table("member_cards")
            .select(fields)
            .eq("id", int(member_id))
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def normalize_member_payload_for_save(data: dict):
    def _value(key, default=None):
        value = data.get(key, default)
        if isinstance(value, str):
            value = value.strip()
        return value

    try:
        idade = _value("idade")
        idade = int(str(idade)) if str(idade).strip() else None
    except Exception:
        idade = None

    try:
        ordem = int(str(_value("ordem", 0) or "0"))
    except Exception:
        ordem = 0

    galeria = data.get("galeria") or []
    if not isinstance(galeria, list):
        galeria = []

    return {
        "nome": _value("nome") or "Sem nome",
        "idade": idade,
        "cargo": (_value("cargo", "membro") or "membro").lower(),
        "meta": _value("meta"),
        "personalidade": _value("personalidade"),
        "habitos": _value("habitos"),
        "gostos": _value("gostos"),
        "hobbies": _value("hobbies"),
        "tags": _value("tags"),
        "stats": _value("stats"),
        "sigil": _value("sigil", "✦") or "✦",
        "imagem_url": _value("imagem_url"),
        "accent_color": _value("accent_color", "#7c3aed") or "#7c3aed",
        "ordem": ordem,
        "galeria": galeria[:16],
    }


def link_discord_to_site_profile(discord_user_id: int, code: str):
    sb = require_supabase()
    if not sb:
        return False, "Supabase não configurado."

    code = str(code or "").strip().upper()
    if not code:
        return False, "Código inválido."

    now_iso = datetime.utcnow().isoformat()

    try:
        # Busca perfil com código válido
        result = (
            sb.table("profiles")
            .select("id, nome, discord_link_code_expires_at")
            .eq("discord_link_code", code)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        profile = rows[0] if rows else None
        if not profile:
            return False, "Código não encontrado."

        expires_at = str(profile.get("discord_link_code_expires_at") or "")
        if expires_at and expires_at < now_iso:
            return False, "Código expirado. Gere um novo no site."

        # Remove vínculo desse discord de outros perfis
        sb.table("profiles").update({"discord_user_id": None}).eq("discord_user_id", int(discord_user_id)).execute()

        # Vincula no perfil correto e limpa código
        sb.table("profiles").update(
            {
                "discord_user_id": int(discord_user_id),
                "discord_link_code": None,
                "discord_link_code_expires_at": None,
            }
        ).eq("id", profile.get("id")).execute()

        # Sincroniza vínculos existentes para evitar "acesso só de um lado"
        profile_id = _safe_int(profile.get("id"))
        if profile_id:
            try:
                sb.table("member_card_links").update(
                    {"profile_id": profile_id, "updated_at": now_iso}
                ).eq("discord_user_id", int(discord_user_id)).eq("status", "active").is_("profile_id", "null").execute()
            except Exception:
                pass

            try:
                sb.table("member_card_links").update(
                    {"discord_user_id": int(discord_user_id), "updated_at": now_iso}
                ).eq("profile_id", profile_id).eq("status", "active").is_("discord_user_id", "null").execute()
            except Exception:
                pass

            try:
                sb.table("member_card_link_requests").update(
                    {"requested_by_profile_id": profile_id}
                ).eq("requested_by_discord_id", int(discord_user_id)).eq("status", "pending").is_("requested_by_profile_id", "null").execute()
            except Exception:
                pass

        return True, f"Conta Discord vinculada ao perfil do site: {profile.get('nome') or profile.get('id')}."
    except Exception as e:
        return False, f"Erro ao vincular conta: {e}"


def get_profile_id_by_discord_user(discord_user_id: int):
    sb = require_supabase()
    if not sb:
        return None
    try:
        result = (
            sb.table("profiles")
            .select("id")
            .eq("discord_user_id", int(discord_user_id))
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0].get("id") if rows else None
    except Exception:
        return None


def get_discord_user_id_by_profile_id(profile_id: int):
    sb = require_supabase()
    if not sb:
        return None
    try:
        result = (
            sb.table("profiles")
            .select("discord_user_id")
            .eq("id", int(profile_id))
            .limit(1)
            .execute()
        )
        rows = result.data or []
        discord_id = rows[0].get("discord_user_id") if rows else None
        return _safe_int(discord_id)
    except Exception:
        return None


def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _status_label(status: str) -> str:
    status = str(status or "").strip().lower()
    if status == "approved":
        return "Aprovada"
    if status == "rejected":
        return "Rejeitada"
    if status == "pending":
        return "Pendente"
    return status or "Desconhecido"


def _request_source_label(source: str) -> str:
    source = str(source or "").strip().lower()
    if source == "discord":
        return "Discord"
    if source == "site":
        return "Site"
    return source or "N/A"


async def send_member_link_status_notification(request_row: dict):
    discord_id = _safe_int(request_row.get("requested_by_discord_id"))
    if not discord_id:
        return

    user = bot.get_user(discord_id)
    if user is None:
        try:
            user = await bot.fetch_user(discord_id)
        except Exception:
            user = None

    if not user:
        return

    member_id = _safe_int(request_row.get("member_card_id"))
    member_name = f"Membro #{member_id}" if member_id else "Membro"

    if member_id:
        card = get_member_card_by_id(member_id)
        if card and card.get("nome"):
            member_name = str(card.get("nome"))

    status = str(request_row.get("status") or "").lower()
    if status == "approved":
        msg = (
            f"✅ Sua solicitação de vínculo **#{request_row.get('id')}** foi aprovada.\n"
            f"Card vinculado: **{member_name}** (ID {member_id}).\n"
            "Você já pode usar os comandos de edição do seu card."
        )
    elif status == "rejected":
        motivo = str(request_row.get("rejected_reason") or "").strip()
        msg = (
            f"❌ Sua solicitação de vínculo **#{request_row.get('id')}** foi rejeitada.\n"
            f"Card: **{member_name}** (ID {member_id})."
        )
        if motivo:
            msg += f"\nMotivo: {motivo}"
    else:
        return

    try:
        await user.send(msg)
    except Exception:
        pass


@tasks.loop(seconds=45)
async def sync_member_link_request_notifications():
    sb = require_supabase()
    if not sb:
        return

    try:
        result = (
            sb.table("member_card_link_requests")
            .select("id, member_card_id, requested_by_discord_id, status, rejected_reason, approved_at")
            .in_("status", ["approved", "rejected"])
            .order("approved_at", desc=True)
            .limit(80)
            .execute()
        )
    except Exception:
        return

    rows = result.data or []
    notified_ids = {str(x) for x in (storage.get("notified_link_request_ids") or [])}
    changed = False

    for row in rows:
        request_id = _safe_int(row.get("id"))
        if not request_id:
            continue

        key = str(request_id)
        if key in notified_ids:
            continue

        await send_member_link_status_notification(row)
        notified_ids.add(key)
        changed = True

    if changed:
        trimmed = list(notified_ids)[-500:]
        storage["notified_link_request_ids"] = trimmed
        save_json(STORAGE_FILE, storage)


class LinkRequestApproveButton(discord.ui.Button):
    def __init__(self, request_id: int):
        super().__init__(label="Aprovar", style=discord.ButtonStyle.success)
        self.request_id = int(request_id)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not has_approver_role(member):
            await interaction.response.send_message("Sem permissão para aprovar vínculo.", ephemeral=True)
            return

        ok, message, status = await processar_aprovacao_vinculo_internal(
            request_id=self.request_id,
            aprovar=True,
            approver_discord_id=member.id,
            motivo="",
        )
        if status in ("approved", "rejected"):
            for item in self.view.children:
                item.disabled = True
            await interaction.response.edit_message(view=self.view)
        await interaction.followup.send(message, ephemeral=True)


class LinkRequestRejectButton(discord.ui.Button):
    def __init__(self, request_id: int):
        super().__init__(label="Rejeitar", style=discord.ButtonStyle.danger)
        self.request_id = int(request_id)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member or not has_approver_role(member):
            await interaction.response.send_message("Sem permissão para rejeitar vínculo.", ephemeral=True)
            return

        ok, message, status = await processar_aprovacao_vinculo_internal(
            request_id=self.request_id,
            aprovar=False,
            approver_discord_id=member.id,
            motivo="Rejeitado via painel Discord",
        )
        if status in ("approved", "rejected"):
            for item in self.view.children:
                item.disabled = True
            await interaction.response.edit_message(view=self.view)
        await interaction.followup.send(message, ephemeral=True)


class LinkRequestApprovalView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(LinkRequestApproveButton(request_id))
        self.add_item(LinkRequestRejectButton(request_id))


def format_recruitment_answer(value):
    if value is None:
        return "-"
    if isinstance(value, (list, tuple)):
        text = ", ".join(str(item) for item in value if str(item).strip())
    elif isinstance(value, dict):
        text = "\n".join(f"{key}: {val}" for key, val in value.items())
    else:
        text = str(value)
    text = text.strip() or "-"
    return text[:1000]


def get_recruitment_form_field_labels():
    sb = require_supabase()
    if not sb:
        return []

    try:
        result = (
            sb.table("recruitment_form_settings")
            .select("campos")
            .eq("ativo", True)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
    except Exception:
        return []

    rows = result.data or []
    campos = rows[0].get("campos") if rows else []
    return campos if isinstance(campos, list) else []


def build_recruitment_description(respostas, fields=None):
    fields = fields or []
    used = set()
    lines = []

    for field in fields:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("id") or "")
        if not field_id or field_id not in respostas:
            continue
        label = str(field.get("label") or field_id)
        lines.append(f"**{label}:** {format_recruitment_answer(respostas.get(field_id))}")
        used.add(field_id)

    for key, value in respostas.items():
        if key in used:
            continue
        label = str(key).replace("_", " ").strip() or "Campo"
        lines.append(f"**{label}:** {format_recruitment_answer(value)}")

    return "\n".join(lines)[:4000] or "-"


def build_recruitment_submission_embed(row):
    respostas = row.get("respostas") or {}
    if not isinstance(respostas, dict):
        respostas = {"respostas": respostas}
    fields = get_recruitment_form_field_labels()

    embed = discord.Embed(
        title="Nova candidatura recebida",
        description=build_recruitment_description(respostas, fields),
        color=get_embed_color(),
        timestamp=datetime.utcnow(),
    )

    embed.set_footer(text="Sistema de recrutamento Iconics")
    return embed


def build_site_log_embed(row):
    title = str(row.get("event_title") or "Atualizacao no site")[:256]
    description = str(row.get("event_description") or "-")[:4000]
    level = str(row.get("level") or "info").lower()
    color = discord.Color.green() if level == "success" else discord.Color.red() if level == "error" else get_embed_color()

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.utcnow(),
    )
    created_at = row.get("created_at")
    if created_at:
        embed.set_footer(text=f"Log do site Iconics | {created_at}")
    else:
        embed.set_footer(text="Log do site Iconics")
    return embed


@tasks.loop(seconds=20)
async def sync_site_logs_to_discord():
    sb = require_supabase()
    if not sb:
        return

    log_channel_id = config.get("log_channel_id")
    if not log_channel_id:
        return

    channel = bot.get_channel(int(log_channel_id))
    if not channel:
        return

    try:
        result = (
            sb.table("discord_logs")
            .select("id, guild_id, channel_id, event_title, event_description, level, created_at")
            .eq("guild_id", "site")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
    except Exception as e:
        log_sync_error("site_logs_fetch", "[SITE LOG SYNC] Erro ao buscar logs do site:", e)
        return

    rows = list(reversed(result.data or []))
    notified_ids = {str(x) for x in (storage.get("notified_site_log_ids") or [])}
    changed = False

    for row in rows:
        log_id = row.get("id")
        if log_id is None:
            continue

        key = str(log_id)
        if key in notified_ids:
            continue

        try:
            await channel.send(embed=build_site_log_embed(row))
            notified_ids.add(key)
            changed = True
        except Exception as e:
            log_sync_error(f"site_log_send_{log_id}", f"[SITE LOG SYNC] Erro ao enviar log #{log_id}:", e)
            continue

    if changed:
        storage["notified_site_log_ids"] = list(notified_ids)[-1500:]
        save_json(STORAGE_FILE, storage)


@tasks.loop(seconds=30)
async def sync_recruitment_submissions_to_discord():
    sb = require_supabase()
    if not sb:
        return

    channel_id = (
        os.getenv("RECRUITMENT_SUBMISSIONS_CHANNEL_ID")
        or config.get("recruitment_submissions_channel_id")
        or config.get("approval_channel_id")
    )
    if not channel_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not channel:
        return

    try:
        result = (
            sb.table("recruitment_submissions")
            .select("id, respostas, status, created_at")
            .eq("status", "novo")
            .order("created_at", desc=True)
            .limit(25)
            .execute()
        )
    except Exception as e:
        log_sync_error("recruitment_fetch", "[RECRUITMENT SYNC] Erro ao buscar candidaturas:", e)
        return

    rows = list(reversed(result.data or []))
    notified_ids = {str(x) for x in (storage.get("notified_recruitment_submission_ids") or [])}
    changed = False

    for row in rows:
        submission_id = row.get("id")
        if submission_id is None:
            continue

        key = str(submission_id)
        if key in notified_ids:
            continue

        try:
            await channel.send(embed=build_recruitment_submission_embed(row))
            notified_ids.add(key)
            changed = True
        except Exception as e:
            log_sync_error(f"recruitment_send_{submission_id}", f"[RECRUITMENT SYNC] Erro ao enviar candidatura #{submission_id}:", e)
            continue

    if changed:
        storage["notified_recruitment_submission_ids"] = list(notified_ids)[-1000:]
        save_json(STORAGE_FILE, storage)


@tasks.loop(seconds=30)
async def sync_pending_site_link_requests_to_discord():
    sb = require_supabase()
    if not sb:
        return

    approval_channel_id = config.get("approval_channel_id")
    if not approval_channel_id:
        return

    channel = bot.get_channel(int(approval_channel_id))
    if not channel:
        return

    try:
        result = (
            sb.table("member_card_link_requests")
            .select("id, member_card_id, requested_by_profile_id, requested_by_discord_id, requested_by_name, request_source, status, requested_at")
            .eq("status", "pending")
            .order("requested_at", desc=True)
            .limit(50)
            .execute()
        )
    except Exception:
        return

    rows = result.data or []
    notified_ids = {str(x) for x in (storage.get("notified_pending_link_request_ids") or [])}
    changed = False

    for row in rows:
        request_id = _safe_int(row.get("id"))
        if not request_id:
            continue

        key = str(request_id)
        if key in notified_ids:
            continue

        member_id = _safe_int(row.get("member_card_id"))
        member_name = f"Membro #{member_id}" if member_id else "Membro"
        card = get_member_card_by_id(member_id) if member_id else None
        if card and card.get("nome"):
            member_name = str(card.get("nome"))

        solicitante = row.get("requested_by_name") or "Sem nome"
        if row.get("requested_by_discord_id"):
            solicitante = f"{solicitante} (Discord {row.get('requested_by_discord_id')})"
        elif row.get("requested_by_profile_id"):
            solicitante = f"{solicitante} (Perfil {row.get('requested_by_profile_id')})"

        embed = discord.Embed(
            title="🔗 Nova solicitação de vínculo",
            description=(
                f"**Pedido:** #{request_id}\n"
                f"**Card:** {member_name} (ID {member_id})\n"
                f"**Solicitante:** {solicitante}\n"
                f"**Origem:** {_request_source_label(row.get('request_source'))}"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.utcnow(),
        )

        try:
            await channel.send(embed=embed, view=LinkRequestApprovalView(request_id))
            notified_ids.add(key)
            changed = True
        except Exception:
            continue

    if changed:
        storage["notified_pending_link_request_ids"] = list(notified_ids)[-800:]
        save_json(STORAGE_FILE, storage)


@bot.command(name="definircodigomembro")
@commands.cooldown(1, 10, commands.BucketType.user)
async def definir_codigo_membro(ctx, member_id: int = None, *, codigo: str = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão para definir código.")
        return

    if not member_id or not codigo:
        await ctx.send("Uso: `!definircodigomembro <id_membro> <codigo>`")
        return

    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    card = get_member_card_by_id(member_id)
    if not card:
        await ctx.send("Membro não encontrado.")
        return

    code_hash = hash_member_access_code(member_id, codigo)

    try:
        sb.table("member_cards").update(
            {
                "access_code_hash": code_hash,
                "access_code_updated_at": datetime.utcnow().isoformat(),
            }
        ).eq("id", member_id).execute()
        await ctx.send(f"✅ Código de acesso definido para **{card.get('nome', f'Membro #{member_id}')}**.")
    except Exception as e:
        await ctx.send(f"Erro ao definir código: {e}")


@bot.command(name="solicitarvinculo")
@commands.cooldown(1, 8, commands.BucketType.user)
async def solicitar_vinculo(ctx, member_id: int = None, *, codigo: str = None):
    if not member_id or not codigo:
        await ctx.send("Uso: `!solicitarvinculo <id_membro> <codigo>`")
        return

    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    card = get_member_card_by_id(member_id)
    if not card:
        await ctx.send("Membro não encontrado.")
        return

    expected_hash = card.get("access_code_hash")
    if not expected_hash:
        await ctx.send("Esse card ainda não possui código configurado. Fale com liderança/staff.")
        return

    incoming_hash = hash_member_access_code(member_id, codigo)
    if incoming_hash != expected_hash:
        await ctx.send("Código inválido para esse membro.")
        return

    linked_profile_id = get_profile_id_by_discord_user(ctx.author.id)

    try:
        existing_query = (
            sb.table("member_card_link_requests")
            .select("id")
            .eq("member_card_id", member_id)
            .eq("requested_by_discord_id", ctx.author.id)
            .eq("status", "pending")
        )
        if linked_profile_id is not None:
            existing_query = existing_query.eq("requested_by_profile_id", linked_profile_id)
        existing = existing_query.limit(1).execute()
        existing_rows = existing.data or []
        if existing_rows and existing_rows[0].get("id"):
            await ctx.send(f"Você já possui uma solicitação pendente: **#{existing_rows[0]['id']}**.")
            return

        payload = {
            "member_card_id": member_id,
            "access_code_hash": incoming_hash,
            "requested_by_profile_id": linked_profile_id,
            "requested_by_discord_id": ctx.author.id,
            "requested_by_name": str(ctx.author),
            "request_source": "discord",
            "status": "pending",
        }

        inserted = sb.table("member_card_link_requests").insert(payload).execute()

        request_id = None
        if inserted and getattr(inserted, "data", None):
            data = inserted.data
            if isinstance(data, list) and data:
                request_id = data[0].get("id")
            elif isinstance(data, dict):
                request_id = data.get("id")

        if request_id is None:
            latest = (
                sb.table("member_card_link_requests")
                .select("id")
                .eq("member_card_id", member_id)
                .eq("requested_by_discord_id", ctx.author.id)
                .eq("status", "pending")
                .order("requested_at", desc=True)
                .limit(1)
                .execute()
            )
            latest_rows = latest.data or []
            if latest_rows:
                request_id = latest_rows[0].get("id")

        await ctx.send(
            f"✅ Solicitação enviada com sucesso. Pedido **#{request_id}** aguardando aprovação de líder/vice/staff."
        )

        if not linked_profile_id:
            await ctx.send(
                "ℹ️ Sua conta Discord ainda não está vinculada ao perfil do site.\n"
                "Para editar no site e no Discord sem perder acesso, use: `!vincularsite <codigo>`."
            )

        if ctx.guild:
            await send_log_embed(
                ctx.guild,
                "🔗 Solicitação de vínculo de card",
                f"**Pedido:** #{request_id}\n**Membro:** {card.get('nome', f'#{member_id}')}\n**Usuário:** {ctx.author.mention}",
                discord.Color.gold(),
            )
    except Exception as e:
        await ctx.send(f"Erro ao enviar solicitação: {e}")


@bot.command(name="vincularsite")
@commands.cooldown(1, 8, commands.BucketType.user)
async def vincular_site(ctx, codigo: str = None):
    if not codigo:
        await ctx.send(
            "Uso: `!vincularsite <codigo>`\n"
            "No site, gere o código em Painel > Vínculo > Vincular Discord."
        )
        return

    ok, message = link_discord_to_site_profile(ctx.author.id, codigo)
    await ctx.send(("✅ " if ok else "❌ ") + message)

    if ok and ctx.guild:
        await send_log_embed(
            ctx.guild,
            "🔐 Conta Discord vinculada ao site",
            f"**Usuário Discord:** {ctx.author.mention}\n**Código:** {codigo}",
            discord.Color.green(),
        )


@bot.command(name="pendenciasvinculo")
@commands.cooldown(1, 5, commands.BucketType.user)
async def pendencias_vinculo(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão para ver pendências de vínculo.")
        return

    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    try:
        result = (
            sb.table("member_card_link_requests")
            .select("id, member_card_id, requested_by_profile_id, requested_by_discord_id, requested_by_name, request_source, status, requested_at")
            .eq("status", "pending")
            .order("requested_at", desc=True)
            .limit(15)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        await ctx.send(f"Erro ao carregar pendências: {e}")
        return

    if not rows:
        await ctx.send("Não há solicitações de vínculo pendentes.")
        return

    linhas = ["**Solicitações pendentes de vínculo (site + discord):**"]
    for row in rows:
        req_id = row.get("id")
        member_id = row.get("member_card_id")
        member_name = f"Membro #{member_id}"
        card = get_member_card_by_id(member_id) if member_id else None
        if card and card.get("nome"):
            member_name = str(card.get("nome"))

        solicitante = row.get("requested_by_name") or "Sem nome"
        if row.get("requested_by_discord_id"):
            solicitante = f"{solicitante} (Discord {row.get('requested_by_discord_id')})"
        elif row.get("requested_by_profile_id"):
            solicitante = f"{solicitante} (Perfil {row.get('requested_by_profile_id')})"

        linhas.append(
            f"• **#{req_id}** | {member_name} (ID {member_id}) | {solicitante} | origem: {_request_source_label(row.get('request_source'))}"
        )

    linhas.append("\nPara aprovar: `!aprovarvinculo <id>`")
    linhas.append("Para rejeitar: `!rejeitarvinculo <id> [motivo]`")
    await ctx.send("\n".join(linhas))


@bot.command(name="statusvinculo")
@commands.cooldown(1, 5, commands.BucketType.user)
async def status_vinculo(ctx):
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    try:
        result = (
            sb.table("member_card_link_requests")
            .select("id, member_card_id, status, request_source, rejected_reason, requested_at, approved_at")
            .eq("requested_by_discord_id", ctx.author.id)
            .order("requested_at", desc=True)
            .limit(10)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        await ctx.send(f"Erro ao consultar status: {e}")
        return

    if not rows:
        await ctx.send("Você não possui solicitações de vínculo.")
        return

    linhas = ["**Suas solicitações de vínculo:**"]
    for row in rows:
        member_id = row.get("member_card_id")
        member_name = f"Membro #{member_id}"
        card = get_member_card_by_id(member_id) if member_id else None
        if card and card.get("nome"):
            member_name = str(card.get("nome"))

        status = _status_label(row.get("status"))
        origem = _request_source_label(row.get("request_source"))
        extra = ""
        if str(row.get("status")).lower() == "rejected" and row.get("rejected_reason"):
            extra = f" | motivo: {row.get('rejected_reason')}"

        linhas.append(
            f"• **#{row.get('id')}** | {member_name} (ID {member_id}) | {status} | origem: {origem}{extra}"
        )

    await ctx.send("\n".join(linhas))


async def processar_aprovacao_vinculo_internal(request_id: int, aprovar: bool, approver_discord_id: int, motivo: str = ""):
    sb = require_supabase()
    if not sb:
        return False, "Supabase não configurado.", None

    try:
        result = (
            sb.table("member_card_link_requests")
            .select("*")
            .eq("id", request_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        req_data = rows[0] if rows else None
        if not req_data:
            return False, "Solicitação não encontrada.", None

        if req_data.get("status") != "pending":
            return False, "Essa solicitação já foi processada.", str(req_data.get("status") or "")

        now = datetime.utcnow().isoformat()

        if not aprovar:
            sb.table("member_card_link_requests").update(
                {
                    "status": "rejected",
                    "rejected_reason": motivo or None,
                    "approved_at": now,
                    "approved_by_discord_id": int(approver_discord_id),
                }
            ).eq("id", request_id).execute()

            try:
                req_data["status"] = "rejected"
                req_data["rejected_reason"] = motivo or None
                await send_member_link_status_notification(req_data)
                notified = {str(x) for x in (storage.get("notified_link_request_ids") or [])}
                notified.add(str(request_id))
                storage["notified_link_request_ids"] = list(notified)[-500:]
                save_json(STORAGE_FILE, storage)
            except Exception:
                pass

            return True, f"❌ Solicitação #{request_id} rejeitada.", "rejected"

        profile_id = _safe_int(req_data.get("requested_by_profile_id"))
        discord_id = _safe_int(req_data.get("requested_by_discord_id"))
        member_card_id = _safe_int(req_data.get("member_card_id"))

        # Completa o outro lado do vínculo quando possível
        if not profile_id and discord_id:
            profile_id = get_profile_id_by_discord_user(discord_id)
        if not discord_id and profile_id:
            discord_id = get_discord_user_id_by_profile_id(profile_id)

        if not profile_id and not discord_id:
            return (
                False,
                "Não foi possível identificar a conta do site nem a conta do Discord da solicitação. "
                "Peça para o usuário usar `!vincularsite <codigo>` e solicitar novamente.",
                None,
            )

        # Persiste IDs resolvidos no pedido para manter consistência entre bot e site
        try:
            sb.table("member_card_link_requests").update(
                {
                    "requested_by_profile_id": profile_id,
                    "requested_by_discord_id": discord_id,
                }
            ).eq("id", request_id).execute()
        except Exception:
            pass

        if profile_id:
            sb.table("member_card_links").update(
                {"status": "revoked", "updated_at": now}
            ).eq("profile_id", profile_id).eq("status", "active").execute()

        if discord_id:
            sb.table("member_card_links").update(
                {"status": "revoked", "updated_at": now}
            ).eq("discord_user_id", discord_id).eq("status", "active").execute()

        sb.table("member_card_links").update(
            {"status": "revoked", "updated_at": now}
        ).eq("member_card_id", member_card_id).eq("status", "active").execute()

        sb.table("member_card_links").insert(
            {
                "member_card_id": member_card_id,
                "profile_id": profile_id,
                "discord_user_id": discord_id,
                "status": "active",
                "can_edit": True,
                "approved_by_discord_id": int(approver_discord_id),
                "created_from_request_id": request_id,
                "created_at": now,
                "updated_at": now,
            }
        ).execute()

        # Garante que o profile esteja ligado ao mesmo discord aprovado
        if profile_id and discord_id:
            try:
                sb.table("profiles").update({"discord_user_id": None}).eq("discord_user_id", int(discord_id)).neq("id", profile_id).execute()
                sb.table("profiles").update({"discord_user_id": int(discord_id)}).eq("id", profile_id).execute()
            except Exception:
                pass

        sb.table("member_card_link_requests").update(
            {
                "status": "approved",
                "approved_at": now,
                "approved_by_discord_id": int(approver_discord_id),
                "rejected_reason": None,
            }
        ).eq("id", request_id).execute()

        try:
            req_data["status"] = "approved"
            req_data["rejected_reason"] = None
            await send_member_link_status_notification(req_data)
            notified = {str(x) for x in (storage.get("notified_link_request_ids") or [])}
            notified.add(str(request_id))
            storage["notified_link_request_ids"] = list(notified)[-500:]
            save_json(STORAGE_FILE, storage)
        except Exception:
            pass

        return True, f"✅ Solicitação #{request_id} aprovada com sucesso.", "approved"
    except Exception as e:
        return False, f"Erro ao processar solicitação: {e}", None


async def processar_aprovacao_vinculo(ctx, request_id: int, aprovar: bool, motivo: str = ""):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão para aprovar vínculo.")
        return

    ok, message, _ = await processar_aprovacao_vinculo_internal(
        request_id=request_id,
        aprovar=aprovar,
        approver_discord_id=ctx.author.id,
        motivo=motivo,
    )
    await ctx.send(message)


@bot.command(name="aprovarvinculo")
@commands.cooldown(1, 5, commands.BucketType.user)
async def aprovar_vinculo(ctx, request_id: int = None):
    if not request_id:
        await ctx.send("Uso: `!aprovarvinculo <id_solicitacao>`")
        return
    await processar_aprovacao_vinculo(ctx, request_id, True)


@bot.command(name="rejeitarvinculo")
@commands.cooldown(1, 5, commands.BucketType.user)
async def rejeitar_vinculo(ctx, request_id: int = None, *, motivo: str = ""):
    if not request_id:
        await ctx.send("Uso: `!rejeitarvinculo <id_solicitacao> [motivo]`")
        return
    await processar_aprovacao_vinculo(ctx, request_id, False, motivo)


@bot.command(name="meuvinculo")
@commands.cooldown(1, 5, commands.BucketType.user)
async def meu_vinculo(ctx):
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    link = is_member_link_editor_by_discord(ctx.author.id)
    if not link:
        await ctx.send(
            "Você não possui vínculo ativo.\nUse `!solicitarvinculo <id_membro> <codigo>` para solicitar."
        )
        return

    if not link.get("can_edit", True):
        await ctx.send("Seu vínculo está ativo, mas sem permissão de edição no momento.")
        return

    member_id = link.get("member_card_id")
    card = get_member_card_by_id(member_id)
    nome = card.get("nome") if card else f"Membro #{member_id}"

    await ctx.send(
        f"🔗 Vínculo ativo com **{nome}** (ID {member_id}).\n"
        "Comandos liberados para você:\n"
        "`!editarmeumembro` (editor 3 etapas)\n"
        "`!editarmeumembro campo | valor` (atalho)\n"
        "`!desvincularmeu`\n"
        "`!vergaleriameu`\n"
        "`!addfotomeu` (com anexos)\n"
        "`!removerfotomeu <indice>`"
    )


@bot.command(name="desvincularmeu")
@commands.cooldown(1, 8, commands.BucketType.user)
async def desvincular_meu(ctx):
    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    link = is_member_link_editor_by_discord(ctx.author.id)
    if not link:
        await ctx.send("Você não possui vínculo ativo para remover.")
        return

    member_id = _safe_int(link.get("member_card_id"))
    member_name = f"Membro #{member_id}" if member_id else "Membro"
    card = get_member_card_by_id(member_id) if member_id else None
    if card and card.get("nome"):
        member_name = str(card.get("nome"))

    now = datetime.utcnow().isoformat()
    try:
        sb.table("member_card_links").update(
            {"status": "revoked", "can_edit": False, "updated_at": now}
        ).eq("discord_user_id", int(ctx.author.id)).eq("status", "active").execute()
    except Exception as e:
        await ctx.send(f"Erro ao desvincular: {e}")
        return

    await ctx.send(f"✅ Vínculo removido com sucesso do card **{member_name}**.")

    if ctx.guild:
        await send_log_embed(
            ctx.guild,
            "🔓 Vínculo removido via Discord",
            f"**Usuário:** {ctx.author.mention}\n**Card:** {member_name}\n**ID:** {member_id}",
            discord.Color.orange(),
        )


@bot.command(name="desvincularmembro", aliases=["desvinularmembro"])
@commands.cooldown(1, 8, commands.BucketType.user)
async def desvincular_membro_staff(ctx, alvo: str = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão para desvincular membros.")
        return

    if not alvo:
        await ctx.send("Uso: `!desvincularmembro <id_card|discord_id|profile_id>`")
        return

    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    now = datetime.utcnow().isoformat()
    target = str(alvo).strip()

    try:
        query = sb.table("member_card_links").update(
            {"status": "revoked", "can_edit": False, "updated_at": now}
        ).eq("status", "active")

        if target.isdigit():
            num = int(target)
            # tenta por card e por discord id
            result_card = query.eq("member_card_id", num).execute()
            revoked_card = len(result_card.data or [])

            query2 = sb.table("member_card_links").update(
                {"status": "revoked", "can_edit": False, "updated_at": now}
            ).eq("status", "active")
            result_discord = query2.eq("discord_user_id", num).execute()
            revoked_discord = len(result_discord.data or [])

            revoked_total = revoked_card + revoked_discord
        else:
            result_profile = query.eq("profile_id", target).execute()
            revoked_total = len(result_profile.data or [])

    except Exception as e:
        await ctx.send(f"Erro ao desvincular: {e}")
        return

    if revoked_total <= 0:
        await ctx.send("Nenhum vínculo ativo encontrado para esse alvo.")
        return

    await ctx.send(f"✅ {revoked_total} vínculo(s) revogado(s) com sucesso.")
    if ctx.guild:
        await send_log_embed(
            ctx.guild,
            "🔓 Vínculo revogado por staff",
            f"**Alvo:** {target}\n**Quantidade:** {revoked_total}\n**Por:** {ctx.author.mention}",
            discord.Color.orange(),
        )


@bot.command(name="editarmeumembro")
@commands.cooldown(1, 8, commands.BucketType.user)
async def editar_meu_membro(ctx, *, entrada: str = None):
    link = is_member_link_editor_by_discord(ctx.author.id)
    if not link:
        await ctx.send("Você não possui vínculo ativo para editar card.")
        return
    if not link.get("can_edit", True):
        await ctx.send("Seu vínculo está sem permissão de edição.")
        return

    member_id = _safe_int(link.get("member_card_id"))
    if not member_id:
        await ctx.send("Vínculo inválido.")
        return

    # Atalho legado: !editarmeumembro campo | valor
    if entrada and "|" in entrada:
        field, value = [part.strip() for part in entrada.split("|", 1)]
        allowed_fields = {
            "nome",
            "idade",
            "cargo",
            "meta",
            "personalidade",
            "habitos",
            "gostos",
            "hobbies",
            "tags",
            "stats",
            "sigil",
            "imagem_url",
            "accent_color",
        }

        if field not in allowed_fields:
            await ctx.send("Campo inválido para edição.")
            return

        update_payload = {field: value}
        if field == "idade":
            try:
                update_payload["idade"] = int(value)
            except Exception:
                await ctx.send("Idade deve ser numérica.")
                return

        sb = require_supabase()
        if not sb:
            await ctx.send("Supabase não configurado.")
            return

        try:
            sb.table("member_cards").update(update_payload).eq("id", member_id).execute()
            await ctx.send(f"✅ Campo `{field}` atualizado no seu card vinculado.")
        except Exception as e:
            await ctx.send(f"Erro ao atualizar card: {e}")
        return

    card = get_member_card_for_edit(member_id)
    if not card:
        await ctx.send("Card vinculado não encontrado.")
        return

    _member_edit_sessions[ctx.author.id] = {
        **card,
        "member_id": member_id,
        "editor_mode": "self",
    }
    await ctx.send(
        f"Editar seu card **{card.get('nome', f'#{member_id}')}** (ID {member_id}) em 3 etapas:",
        view=MemberEditStep1View(ctx.author.id),
    )


@bot.command(name="editarmembro")
@commands.cooldown(1, 8, commands.BucketType.user)
async def editar_membro_staff(ctx, member_id: int = None):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Sem permissão para editar qualquer card.")
        return

    if not member_id:
        await ctx.send("Uso: `!editarmembro <id_membro>`")
        return

    card = get_member_card_for_edit(member_id)
    if not card:
        await ctx.send("Membro não encontrado.")
        return

    _member_edit_sessions[ctx.author.id] = {
        **card,
        "member_id": int(member_id),
        "editor_mode": "staff",
    }
    await ctx.send(
        f"Edição do card **{card.get('nome', f'#{member_id}')}** (ID {member_id}) iniciada em 3 etapas:",
        view=MemberEditStep1View(ctx.author.id),
    )


@bot.command(name="addfotomeu")
@commands.cooldown(1, 10, commands.BucketType.user)
async def add_foto_meu(ctx):
    if not ctx.message.attachments:
        await ctx.send("Envie esse comando com anexos de imagem para adicionar na galeria.")
        return

    link = is_member_link_editor_by_discord(ctx.author.id)
    if not link:
        await ctx.send("Você não possui vínculo ativo para editar card.")
        return
    if not link.get("can_edit", True):
        await ctx.send("Seu vínculo está sem permissão de edição.")
        return

    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    member_id = link.get("member_card_id")
    card = get_member_card_by_id(member_id)
    if not card:
        await ctx.send("Card não encontrado.")
        return

    galeria = card.get("galeria") or []
    for attachment in ctx.message.attachments:
        if len(galeria) >= 16:
            break
        if attachment.content_type and not attachment.content_type.startswith("image/"):
            continue
        galeria.append(attachment.url)

    try:
        sb.table("member_cards").update({"galeria": galeria[:16]}).eq("id", member_id).execute()
        await ctx.send(f"✅ Galeria atualizada. Total de fotos: {len(galeria[:16])}/16.")
    except Exception as e:
        await ctx.send(f"Erro ao atualizar galeria: {e}")


@bot.command(name="removerfotomeu")
@commands.cooldown(1, 8, commands.BucketType.user)
async def remover_foto_meu(ctx, index: int = None):
    if index is None:
        await ctx.send("Uso: `!removerfotomeu <indice>`")
        return

    link = is_member_link_editor_by_discord(ctx.author.id)
    if not link:
        await ctx.send("Você não possui vínculo ativo para editar card.")
        return
    if not link.get("can_edit", True):
        await ctx.send("Seu vínculo está sem permissão de edição.")
        return

    sb = require_supabase()
    if not sb:
        await ctx.send("Supabase não configurado.")
        return

    member_id = link.get("member_card_id")
    card = get_member_card_by_id(member_id)
    if not card:
        await ctx.send("Card não encontrado.")
        return

    galeria = card.get("galeria") or []
    if index < 1 or index > len(galeria):
        await ctx.send(f"Índice inválido. Sua galeria tem {len(galeria)} foto(s).")
        return

    galeria.pop(index - 1)

    try:
        sb.table("member_cards").update({"galeria": galeria}).eq("id", member_id).execute()
        await ctx.send("✅ Foto removida da sua galeria.")
    except Exception as e:
        await ctx.send(f"Erro ao remover foto: {e}")


@bot.command(name="vergaleriameu")
@commands.cooldown(1, 8, commands.BucketType.user)
async def ver_galeria_meu(ctx):
    link = is_member_link_editor_by_discord(ctx.author.id)
    if not link:
        await ctx.send("Você não possui vínculo ativo.")
        return
    if not link.get("can_edit", True):
        await ctx.send("Seu vínculo está ativo, mas sem permissão de edição.")
        return

    card = get_member_card_by_id(link.get("member_card_id"))
    if not card:
        await ctx.send("Card não encontrado.")
        return

    galeria = card.get("galeria") or []
    nome = card.get("nome", "Membro")
    member_id = int(card.get("id"))

    if not galeria:
        await ctx.send(f"**{nome}** não possui imagens na galeria.")
        return

    linhas = [f"**Galeria de {nome}** (ID do card: {member_id})"]
    for i, url in enumerate(galeria, start=1):
        linhas.append(f"`ID {i}` -> {url}")

    linhas.append(f"\nSite: https://iconics-jade.vercel.app/membro/{member_id}")
    await ctx.send("\n".join(linhas[:22]))



TOKEN = os.getenv("DISCORD_TOKEN") or config.get("token")
TOKEN_SOURCE = "DISCORD_TOKEN" if os.getenv("DISCORD_TOKEN") else "config.json"

if not TOKEN:
    raise RuntimeError("Token não encontrado no ambiente nem no config.json.")

print(f"[STARTUP] Iniciando bot usando token de {TOKEN_SOURCE}.", flush=True)

try:
    bot.run(TOKEN)
except discord.LoginFailure as e:
    print("[STARTUP] Falha ao conectar no Discord: token invalido ou resetado.", flush=True)
    raise
except discord.PrivilegedIntentsRequired as e:
    print(
        "[STARTUP] Falha ao conectar no Discord: intents privilegiadas precisam estar ativadas no Discord Developer Portal.",
        flush=True,
    )
    raise



