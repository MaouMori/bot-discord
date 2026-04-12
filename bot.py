import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from io import BytesIO
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
    save_json(STORAGE_FILE, storage)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


def get_embed_color() -> discord.Color:
    return discord.Color(config.get("embed_color", 5793266))


def get_pending_requests() -> dict[str, Any]:
    global storage
    if "pending_requests" not in storage:
        storage["pending_requests"] = {}
    return storage["pending_requests"]


def persist_storage() -> None:
    global storage
    save_json(STORAGE_FILE, storage)


def has_approver_role(member: discord.Member) -> bool:
    approver_role_id = config["approver_role_id"]
    return any(role.id == approver_role_id for role in member.roles)
    def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
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
    width, height = 1100, 420
    background = Image.new("RGBA", (width, height), (18, 10, 28, 255))
    draw = ImageDraw.Draw(background)

    # fundo com camadas
    draw.rounded_rectangle(
        (20, 20, width - 20, height - 20),
        radius=35,
        fill=(32, 18, 52, 255),
        outline=(130, 82, 255, 255),
        width=3
    )

    draw.rounded_rectangle(
        (45, 45, width - 45, height - 45),
        radius=28,
        fill=(24, 14, 40, 230)
    )

    # brilhos decorativos
    draw.ellipse((760, -80, 1100, 240), fill=(110, 60, 200, 80))
    draw.ellipse((-120, 240, 220, 560), fill=(180, 100, 255, 60))
    draw.ellipse((840, 260, 1080, 500), fill=(255, 255, 255, 18))

    # avatar
    avatar_asset = member.display_avatar.replace(size=256)
    avatar_bytes = BytesIO(await avatar_asset.read())
    avatar = Image.open(avatar_bytes).convert("RGBA").resize((220, 220))

    mask = Image.new("L", (220, 220), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, 220, 220), fill=255)

    avatar_circle = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    avatar_circle.paste(avatar, (0, 0), mask=mask)

    # aro do avatar
    draw.ellipse((78, 98, 322, 342), fill=(150, 100, 255, 255))
    draw.ellipse((88, 108, 312, 332), fill=(28, 16, 46, 255))
    background.paste(avatar_circle, (90, 110), avatar_circle)

    # textos
    title_font = load_font(54)
    name_font = load_font(42)
    small_font = load_font(24)

    draw.text((370, 90), "BEM-VINDO(A)", font=title_font, fill=(245, 240, 255, 255))
    draw.text((372, 150), f"{member.name}", font=name_font, fill=(180, 140, 255, 255))
    draw.text(
        (370, 225),
        f"ao servidor {member.guild.name}",
        font=small_font,
        fill=(230, 220, 255, 220)
    )
    draw.text(
        (370, 268),
        "Faça seu registro para receber seu cargo.",
        font=small_font,
        fill=(255, 255, 255, 210)
    )

    # faixa inferior
    draw.rounded_rectangle(
        (360, 315, 980, 365),
        radius=18,
        fill=(115, 65, 235, 230)
    )
    draw.text(
        (385, 327),
        "Clique no botão de registro no canal indicado pela staff",
        font=small_font,
        fill=(255, 255, 255, 255)
    )

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
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Permissão inválida.", ephemeral=True)
            return

        if not has_approver_role(interaction.user):
            await interaction.response.send_message(
                "Você não tem permissão para aprovar solicitações.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Servidor não encontrado.", ephemeral=True)
            return

        pending = get_pending_requests()
        request = pending.get(str(self.target_user_id))

        if request is None:
            await interaction.response.send_message(
                "Essa solicitação já foi processada ou não existe mais.",
                ephemeral=True
            )
            return

        member = guild.get_member(self.target_user_id)
        if member is None:
            await interaction.response.send_message(
                "Não encontrei o membro no servidor.",
                ephemeral=True
            )
            return

        default_role = guild.get_role(config["default_role_id"])
        requested_role = guild.get_role(request["requested_role_id"])
        roles_without_default = config.get("roles_without_default", [])

        if requested_role is None:
            await interaction.response.send_message(
                "Cargo solicitado não encontrado no config.json.",
                ephemeral=True
            )
            return

        try:
            if requested_role.id in roles_without_default:
                await member.add_roles(
                    requested_role,
                    reason="Registro aprovado"
                )
            else:
                if default_role is None:
                    await interaction.response.send_message(
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
        except discord.Forbidden:
            await interaction.response.send_message(
                "O bot não conseguiu editar cargos ou nickname. "
                "Verifique permissões e a posição do cargo do bot.",
                ephemeral=True
            )
            return

        pending.pop(str(self.target_user_id), None)
        persist_storage()

        try:
            await member.send(
                f"Seu registro foi aprovado em **{guild.name}**.\n"
                f"Cargo recebido: **{requested_role.name}**\n"
                f"Recrutador informado: **{request['recruiter_name']}**\n"
                f"Nickname definido: **{request['character_name']} | {request['character_id']}**"
            )
        except discord.HTTPException:
            pass

        await interaction.response.edit_message(
            content=f"Solicitação aprovada por {interaction.user.mention}.",
            embed=None,
            view=None
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

        guild = interaction.guild
        pending = get_pending_requests()
        request = pending.get(str(self.target_user_id))

        if request is None:
            await interaction.response.send_message(
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

        await interaction.response.edit_message(
            content=f"Solicitação recusada por {interaction.user.mention}.",
            embed=None,
            view=None
        )


class ApprovalView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.add_item(ApproveButton(target_user_id))
        self.add_item(RejectButton(target_user_id))


@bot.event
async def on_ready():
    bot.add_view(RegisterView())
    print(f"Bot conectado como {bot.user}")


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


@bot.command()
async def teste(ctx):
    await ctx.send("to vivo")

@bot.event
async def on_member_join(member: discord.Member):
    welcome_channel_id = config.get("welcome_channel_id", 0)
    welcome_channel = member.guild.get_channel(welcome_channel_id)

    if welcome_channel is None:
        return

    card = await create_welcome_card(member)
    file = discord.File(card, filename="welcome.png")

    embed = discord.Embed(
        title="Novo membro chegou",
        description=(
            f"{member.mention}, seja muito bem-vindo(a) a **{member.guild.name}**.\n\n"
            f"Vá até <#{config.get('registration_channel_id')}> e faça seu registro."
        ),
        color=get_embed_color()
    )
    embed.set_image(url="attachment://welcome.png")

    await welcome_channel.send(embed=embed, file=file)


token = os.getenv("DISCORD_TOKEN", config["token"])
bot.run(token)