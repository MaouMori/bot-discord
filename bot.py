import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from io import BytesIO
from datetime import datetime

print(">>> ARQUIVO ATUAL <<<", os.path.abspath(__file__))
print(">>> TESTE NOVO CARREGADO <<<")

from PIL import Image, ImageDraw, ImageFont
import discord
from discord.ext import commands


def keep_alive():
    port = int(os.environ.get("PORT", 10000))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot online")

    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


threading.Thread(target=keep_alive, daemon=True).start()

CONFIG_FILE = "config.json"
STORAGE_FILE = "storage.json"


def load_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


config = load_json(CONFIG_FILE)
storage = load_json(STORAGE_FILE)

if "pending_requests" not in storage:
    storage["pending_requests"] = {}

if "approved_members" not in storage:
    storage["approved_members"] = []

save_json(STORAGE_FILE, storage)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")


def get_embed_color() -> discord.Color:
    return discord.Color(config.get("embed_color", 5793266))


def get_pending_requests() -> dict[str, Any]:
    global storage
    if "pending_requests" not in storage:
        storage["pending_requests"] = {}
    return storage["pending_requests"]


def get_approved_members() -> list[dict[str, Any]]:
    global storage
    if "approved_members" not in storage:
        storage["approved_members"] = []
    return storage["approved_members"]


def persist_storage() -> None:
    global storage
    save_json(STORAGE_FILE, storage)


def has_approver_role(member: discord.Member) -> bool:
    approver_role_ids = config.get("approver_role_ids")

    if approver_role_ids is None:
        single_role_id = config.get("approver_role_id")
        approver_role_ids = [single_role_id] if single_role_id else []

    return any(role.id in approver_role_ids for role in member.roles)


def load_font(size: int):
    possible_fonts = [
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
        "arial.ttf"
    ]

    for font_name in possible_fonts:
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue

    return ImageFont.load_default()


async def create_welcome_card(member: discord.Member) -> BytesIO:
    width, height = 1200, 500
    background = Image.new("RGBA", (width, height), (10, 8, 18, 255))
    draw = ImageDraw.Draw(background)

    draw.rounded_rectangle(
        (20, 20, width - 20, height - 20),
        radius=38,
        fill=(18, 14, 30, 255),
        outline=(120, 70, 255, 180),
        width=3
    )

    draw.rounded_rectangle(
        (40, 40, width - 40, height - 40),
        radius=30,
        fill=(14, 11, 24, 255)
    )

    draw.ellipse((780, -80, 1180, 280), fill=(90, 45, 180, 70))
    draw.ellipse((-120, 260, 240, 620), fill=(120, 70, 255, 60))
    draw.ellipse((900, 320, 1180, 560), fill=(255, 255, 255, 18))

    draw.rounded_rectangle(
        (70, 80, 78, 420),
        radius=8,
        fill=(140, 90, 255, 255)
    )

    avatar_asset = member.display_avatar.replace(size=256)
    avatar_bytes = BytesIO(await avatar_asset.read())
    avatar = Image.open(avatar_bytes).convert("RGBA").resize((220, 220))

    mask = Image.new("L", (220, 220), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, 220, 220), fill=255)

    avatar_circle = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    avatar_circle.paste(avatar, (0, 0), mask=mask)

    draw.ellipse((110, 140, 350, 380), fill=(122, 72, 255, 255))
    draw.ellipse((120, 150, 340, 370), fill=(24, 18, 38, 255))
    background.paste(avatar_circle, (120, 150), avatar_circle)

    title_font = load_font(58)
    name_font = load_font(40)
    text_font = load_font(24)
    small_font = load_font(20)

    draw.text(
        (410, 115),
        "BEM-VINDO(A)",
        font=title_font,
        fill=(248, 244, 255, 255)
    )

    draw.text(
        (412, 185),
        f"{member.name}",
        font=name_font,
        fill=(170, 130, 255, 255)
    )

    draw.text(
        (410, 250),
        f"ao servidor {member.guild.name}",
        font=text_font,
        fill=(220, 220, 240, 235)
    )

    draw.text(
        (410, 292),
        "Faça seu registro para receber seu cargo.",
        font=text_font,
        fill=(245, 245, 255, 210)
    )

    draw.rounded_rectangle(
        (405, 355, 1035, 405),
        radius=16,
        fill=(110, 60, 235, 230)
    )

    draw.text(
        (430, 368),
        "Clique no botão de registro no canal indicado pela staff",
        font=small_font,
        fill=(255, 255, 255, 255)
    )

    draw.arc((1020, 55, 1130, 165), start=0, end=270, fill=(140, 90, 255, 180), width=3)
    draw.arc((980, 315, 1120, 455), start=180, end=20, fill=(140, 90, 255, 120), width=3)

    output = BytesIO()
    background.save(output, format="PNG")
    output.seek(0)
    return output


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

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Agora escolha o cargo que deseja solicitar:",
            ephemeral=True,
            view=RoleSelectionView(
                user_id=interaction.user.id,
                character_name=str(self.character_name).strip(),
                character_id=str(self.character_id).strip(),
                recruiter_name=str(self.recruiter_name).strip()
            )
        )


class RoleSelect(discord.ui.Select):
    def __init__(
        self,
        user_id: int,
        character_name: str,
        character_id: str,
        recruiter_name: str
    ):
        self.user_id = user_id
        self.character_name = character_name
        self.character_id = character_id
        self.recruiter_name = recruiter_name

        options = [
            discord.SelectOption(label=role_name, value=str(role_id))
            for role_name, role_id in config["requestable_roles"].items()
        ]

        super().__init__(
            placeholder="Selecione o cargo desejado",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Esse menu não é seu.",
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

        selected_role_id = int(self.values[0])
        selected_role_name = next(
            (
                role_name
                for role_name, role_id in config["requestable_roles"].items()
                if role_id == selected_role_id
            ),
            "Desconhecido"
        )

        pending[request_key] = {
            "user_id": interaction.user.id,
            "character_name": self.character_name,
            "character_id": self.character_id,
            "recruiter_name": self.recruiter_name,
            "requested_role_id": selected_role_id,
            "requested_role_name": selected_role_name
        }
        persist_storage()

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Esse comando só funciona dentro do servidor.",
                ephemeral=True
            )
            return

        approval_channel = guild.get_channel(config["approval_channel_id"])
        if approval_channel is None:
            await interaction.response.send_message(
                "Canal de aprovação não encontrado. Verifique o config.json.",
                ephemeral=True
            )
            return

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

        await approval_channel.send(
            embed=embed,
            view=ApprovalView(target_user_id=interaction.user.id)
        )

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
            view=None
        )


class RoleSelectionView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
        character_name: str,
        character_id: str,
        recruiter_name: str
    ):
        super().__init__(timeout=300)
        self.add_item(
            RoleSelect(user_id, character_name, character_id, recruiter_name)
        )


class RegisterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Registrar",
            style=discord.ButtonStyle.primary,
            custom_id="persistent_register_button"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RegistrationModal())


class RegisterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RegisterButton())


class ApproveButton(discord.ui.Button):
    def __init__(self, target_user_id: int):
        super().__init__(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            custom_id=f"approve_request_{target_user_id}"
        )
        self.target_user_id = target_user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()

            if not isinstance(interaction.user, discord.Member):
                await interaction.followup.send("Permissão inválida.", ephemeral=True)
                return

            if not has_approver_role(interaction.user):
                await interaction.followup.send(
                    "Você não tem permissão para aprovar solicitações.",
                    ephemeral=True
                )
                return

            guild = interaction.guild
            if guild is None:
                await interaction.followup.send("Servidor não encontrado.", ephemeral=True)
                return

            pending = get_pending_requests()
            request = pending.get(str(self.target_user_id))

            if request is None:
                await interaction.followup.send(
                    "Essa solicitação já foi processada ou não existe mais.",
                    ephemeral=True
                )
                return

            member = guild.get_member(self.target_user_id)
            if member is None:
                await interaction.followup.send(
                    "Não encontrei o membro no servidor.",
                    ephemeral=True
                )
                return

            default_role = guild.get_role(config["default_role_id"])
            requested_role = guild.get_role(request["requested_role_id"])
            roles_without_default = config.get("roles_without_default", [])

            if requested_role is None:
                await interaction.followup.send(
                    "Cargo solicitado não encontrado no config.json.",
                    ephemeral=True
                )
                return

            if requested_role.id in roles_without_default:
                await member.add_roles(
                    requested_role,
                    reason="Registro aprovado"
                )
            else:
                if default_role is None:
                    await interaction.followup.send(
                        "Cargo padrão não encontrado no config.json.",
                        ephemeral=True
                    )
                    return

                await member.add_roles(
                    default_role,
                    requested_role,
                    reason="Registro aprovado"
                )

            await member.edit(
                nick=f'{request["character_name"]} | {request["character_id"]}',
                reason="Registro aprovado"
            )

            approved_members = get_approved_members()
            approved_members.append({
                "user_id": member.id,
                "discord_name": str(member),
                "character_name": request["character_name"],
                "character_id": request["character_id"],
                "recruiter_name": request["recruiter_name"],
                "requested_role_name": request["requested_role_name"],
                "approved_by": str(interaction.user),
                "approved_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            })

            pending.pop(str(self.target_user_id), None)
            persist_storage()

            await send_log_embed(
                guild,
                "✅ Solicitação aprovada",
                (
                    f"**Membro:** {member.mention}\n"
                    f"**Nome RP:** {request['character_name']}\n"
                    f"**ID:** {request['character_id']}\n"
                    f"**Cargo recebido:** {requested_role.name}\n"
                    f"**Recrutador:** {request['recruiter_name']}\n"
                    f"**Aprovado por:** {interaction.user.mention}"
                ),
                discord.Color.green()
            )

            try:
                await member.send(
                    f"Seu registro foi aprovado em **{guild.name}**.\n"
                    f"Cargo recebido: **{requested_role.name}**\n"
                    f"Recrutador informado: **{request['recruiter_name']}**\n"
                    f"Nickname definido: **{request['character_name']} | {request['character_id']}**"
                )
            except discord.HTTPException:
                pass

            await interaction.message.edit(
                content=f"Solicitação aprovada por {interaction.user.mention}.",
                embed=None,
                view=None
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "O bot não conseguiu editar cargos ou nickname. Verifique permissões e a posição do cargo do bot.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Erro ao aprovar: {e}",
                ephemeral=True
            )


class RejectReasonModal(discord.ui.Modal, title="Motivo da recusa"):
    reason = discord.ui.TextInput(
        label="Motivo",
        placeholder="Explique o motivo da recusa",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True
    )

    def __init__(self, target_user_id: int):
        super().__init__()
        self.target_user_id = target_user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("Permissão inválida.", ephemeral=True)
                return

            if not has_approver_role(interaction.user):
                await interaction.response.send_message(
                    "Você não tem permissão para recusar solicitações.",
                    ephemeral=True
                )
                return

            guild = interaction.guild
            pending = get_pending_requests()
            request = pending.get(str(self.target_user_id))

            if request is None:
                await interaction.response.send_message(
                    "Essa solicitação já foi processada ou não existe mais.",
                    ephemeral=True
                )
                return

            member = guild.get_member(self.target_user_id) if guild else None
            reason_text = str(self.reason).strip()

            pending.pop(str(self.target_user_id), None)
            persist_storage()

            if member and guild:
                try:
                    await member.send(
                        f"Sua solicitação de registro em **{guild.name}** foi recusada.\n\n"
                        f"**Motivo:** {reason_text}"
                    )
                except discord.HTTPException:
                    pass

            if guild:
                await send_log_embed(
                    guild,
                    "❌ Solicitação recusada",
                    (
                        f"**Membro:** <@{self.target_user_id}>\n"
                        f"**Nome RP:** {request['character_name']}\n"
                        f"**ID:** {request['character_id']}\n"
                        f"**Cargo solicitado:** {request['requested_role_name']}\n"
                        f"**Recrutador:** {request['recruiter_name']}\n"
                        f"**Recusado por:** {interaction.user.mention}\n"
                        f"**Motivo:** {reason_text}"
                    ),
                    discord.Color.red()
                )

            await interaction.response.edit_message(
                content=f"Solicitação recusada por {interaction.user.mention}.\nMotivo: {reason_text}",
                embed=None,
                view=None
            )

        except Exception as e:
            await interaction.response.send_message(
                f"Erro ao recusar: {e}",
                ephemeral=True
            )


class RejectButton(discord.ui.Button):
    def __init__(self, target_user_id: int):
        super().__init__(
            label="Recusar",
            style=discord.ButtonStyle.danger,
            custom_id=f"reject_request_{target_user_id}"
        )
        self.target_user_id = target_user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Permissão inválida.", ephemeral=True)
            return

        if not has_approver_role(interaction.user):
            await interaction.response.send_message(
                "Você não tem permissão para recusar solicitações.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(RejectReasonModal(self.target_user_id))
    def __init__(self, target_user_id: int):
        super().__init__(
            label="Recusar",
            style=discord.ButtonStyle.danger,
            custom_id=f"reject_request_{target_user_id}"
        )
        self.target_user_id = target_user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()

            if not isinstance(interaction.user, discord.Member):
                await interaction.followup.send("Permissão inválida.", ephemeral=True)
                return

            if not has_approver_role(interaction.user):
                await interaction.followup.send(
                    "Você não tem permissão para recusar solicitações.",
                    ephemeral=True
                )
                return

            guild = interaction.guild
            pending = get_pending_requests()
            request = pending.get(str(self.target_user_id))

            if request is None:
                await interaction.followup.send(
                    "Essa solicitação já foi processada ou não existe mais.",
                    ephemeral=True
                )
                return

            pending.pop(str(self.target_user_id), None)
            persist_storage()

            if guild:
                member = guild.get_member(self.target_user_id)
                if member:
                    try:
                        await member.send(
                            f"Sua solicitação de registro em **{guild.name}** foi recusada."
                        )
                    except discord.HTTPException:
                        pass

            await interaction.message.edit(
                content=f"Solicitação recusada por {interaction.user.mention}.",
                embed=None,
                view=None
            )

        except Exception as e:
            await interaction.followup.send(
                f"Erro ao recusar: {e}",
                ephemeral=True
            )


class ApprovalView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.add_item(ApproveButton(target_user_id))
        self.add_item(RejectButton(target_user_id))


@bot.event
async def on_ready():
    bot.add_view(RegisterView())
    print(f"Bot conectado como {bot.user} | ID: {bot.user.id}")


@bot.command(name="painelregistro")
async def painel_registro(ctx):
    embed = discord.Embed(
        title="Registro de Membros",
        description=(
            "Clique no botão abaixo para iniciar seu registro.\n\n"
            "Você vai informar o nome do personagem, o ID, quem recrutou você "
            "e depois escolher o cargo que deseja solicitar."
        ),
        color=discord.Color.purple()
    )

    await ctx.send(embed=embed, view=RegisterView())


@bot.command(name="aceitos")
async def listar_aceitos(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Você não tem permissão para ver os membros aceitos.")
        return

    approved_members = get_approved_members()

    if not approved_members:
        await ctx.send("Ainda não há membros aprovados registrados.")
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
    embed.set_footer(text=f"Mostrando os últimos {min(len(approved_members), 15)} aprovados")
    await ctx.send(embed=embed)


@bot.command(name="aceitoslimpar")
async def limpar_aceitos(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Você não tem permissão para limpar a lista.")
        return

    storage["approved_members"] = []
    persist_storage()
    await ctx.send("Lista de membros aceitos limpa com sucesso.")


@bot.command(name="rankingrecrutador")
async def ranking_recrutador(ctx):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Você não tem permissão para ver o ranking.")
        return

    approved_members = get_approved_members()

    if not approved_members:
        await ctx.send("Ainda não há membros aprovados para montar o ranking.")
        return

    ranking = {}

    for member_data in approved_members:
        recruiter = member_data.get("recruiter_name", "Desconhecido").strip()
        if not recruiter:
            recruiter = "Desconhecido"

        recruiter_key = recruiter.lower()

        if recruiter_key not in ranking:
            ranking[recruiter_key] = {
                "name": recruiter,
                "count": 0
            }

        ranking[recruiter_key]["count"] += 1

    ranking_ordenado = sorted(
        ranking.values(),
        key=lambda x: x["count"],
        reverse=True
    )

    embed = discord.Embed(
        title="Ranking de recrutadores",
        color=get_embed_color()
    )

    linhas = []
    for i, item in enumerate(ranking_ordenado[:15], start=1):
        linhas.append(f"**{i}.** {item['name']} — {item['count']} aprovado(s)")

    embed.description = "\n".join(linhas)
    embed.set_footer(text=f"Total de recrutadores no ranking: {len(ranking_ordenado)}")
    await ctx.send(embed=embed)


@bot.command(name="recrutadorinfo")
async def recrutador_info(ctx, *, nome: str):
    if not isinstance(ctx.author, discord.Member) or not has_approver_role(ctx.author):
        await ctx.send("Você não tem permissão para consultar recrutadores.")
        return

    approved_members = get_approved_members()

    if not approved_members:
        await ctx.send("Ainda não há membros aprovados registrados.")
        return

    nome_busca = nome.strip().lower()
    encontrados = [
        member_data for member_data in approved_members
        if member_data.get("recruiter_name", "").strip().lower() == nome_busca
    ]

    if not encontrados:
        await ctx.send(f"Nenhum aprovado foi encontrado para o recrutador **{nome}**.")
        return

    embed = discord.Embed(
        title=f"Informações do recrutador: {nome}",
        color=get_embed_color()
    )

    linhas = []
    for i, member_data in enumerate(encontrados[-15:], start=1):
        linhas.append(
            f"**{i}.** {member_data['character_name']} | {member_data['character_id']} "
            f"- {member_data['requested_role_name']} "
            f"({member_data['approved_at']})"
        )

    embed.add_field(
        name="Total de aprovados",
        value=str(len(encontrados)),
        inline=False
    )
    embed.add_field(
        name="Últimos aprovados",
        value="\n".join(linhas),
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("pong")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="📘 Central de Comandos",
        description="Aqui estão todos os comandos disponíveis no sistema:",
        color=get_embed_color()
    )

    embed.add_field(
        name="📝 Registro",
        value=(
            "`!painelregistro` → Abre o painel de registro"
        ),
        inline=False
    )

    embed.add_field(
    name="👑 Staff",
    value=(
        "`!aceitos` → Lista membros aprovados\n"
        "`!aceitoslimpar` → Limpa lista de aprovados\n"
        "`!rankingrecrutador` → Ranking de recrutadores\n"
        "`!recrutadorinfo <nome>` → Informações de um recrutador\n"
        "`Recusar` → Agora pede motivo da recusa"
    ),
    inline=False
    )

    embed.add_field(
        name="⚙️ Utilidades",
        value=(
            "`!ping` → Testa se o bot está online\n"
            "`!help` → Mostra esta central de comandos"
        ),
        inline=False
    )

    embed.set_footer(
        text=f"Solicitado por {ctx.author}",
        icon_url=ctx.author.display_avatar.url
    )

    await ctx.send(embed=embed)


@bot.event
async def on_member_join(member: discord.Member):
    print(f"ENTROU: {member.name}")
        await send_log_embed(
        member.guild,
        "👋 Novo membro entrou",
        f"**Membro:** {member.mention}\n**Nome:** {member.name}",
        discord.Color.gold()
    )

    welcome_channel = member.guild.get_channel(config.get("welcome_channel_id"))

    if welcome_channel is None:
        print("Canal não encontrado")
        return

    try:
        card = await create_welcome_card(member)
        file = discord.File(card, filename="welcome.png")

        embed = discord.Embed(
            description=f"{member.mention} bem-vindo(a)!",
            color=get_embed_color()
        )
        embed.set_image(url="attachment://welcome.png")

        await welcome_channel.send(embed=embed, file=file)
        print("Card enviado com sucesso")

    except Exception as e:
        print(f"ERRO NO CARD: {e}")

def get_log_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    if guild is None:
        return None

    log_channel_id = config.get("log_channel_id")
    if not log_channel_id:
        return None

    channel = guild.get_channel(log_channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel

    return None


async def send_log_embed(guild: discord.Guild | None, title: str, description: str, color: discord.Color | None = None):
    log_channel = get_log_channel(guild)
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


TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)