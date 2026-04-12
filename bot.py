import json
import os
from typing import Any

import discord
from discord.ext import commands


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

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Agora escolha o cargo que deseja solicitar:",
            ephemeral=True,
            view=RoleSelectionView(
                user_id=interaction.user.id,
                character_name=str(self.character_name).strip(),
                character_id=str(self.character_id).strip()
            )
        )


class RoleSelect(discord.ui.Select):
    def __init__(self, user_id: int, character_name: str, character_id: str):
        self.user_id = user_id
        self.character_name = character_name
        self.character_id = character_id

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
    def __init__(self, user_id: int, character_name: str, character_id: str):
        super().__init__(timeout=300)
        self.add_item(RoleSelect(user_id, character_name, character_id))


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

        if default_role is None:
            await interaction.response.send_message(
                "Cargo padrão não encontrado no config.json.",
                ephemeral=True
            )
            return

        if requested_role is None:
            await interaction.response.send_message(
                "Cargo solicitado não encontrado no config.json.",
                ephemeral=True
            )
            return

        try:
            await member.add_roles(default_role, requested_role, reason="Registro aprovado")
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
    

class RegisterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Registrar",
            style=discord.ButtonStyle.primary,
            custom_id="persistent_register_button"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RegistrationModal())


class RegisterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RegisterButton())


@bot.command(name="painelregistro")
async def painel_registro(ctx):
    embed = discord.Embed(
        title="Registro de Membros",
        description=(
            "Clique no botão abaixo para iniciar seu registro.\n\n"
            "Você vai informar o nome do personagem, o ID e escolher o cargo que deseja solicitar."
        ),
        color=discord.Color.purple()
    )

    await ctx.send(embed=embed, view=RegisterView())

@bot.command()
async def teste(ctx):
    await ctx.send("to vivo")

bot.run(config["token"])