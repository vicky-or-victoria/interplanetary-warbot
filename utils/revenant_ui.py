import discord


DIVIDER = "━━━━━━━━━━━━━━━━━━"

REVENANT_COLORS = {
    "default": 0x34495E,
    "info": 0x34495E,
    "active": 0x2ECC71,
    "success": 0x2ECC71,
    "warning": 0xD6A536,
    "danger": 0xC0392B,
    "error": 0xC0392B,
    "gm": 0x6C5CE7,
    "admin": 0x6C5CE7,
}


def get_revenant_color(color_type: str = "default") -> int:
    return REVENANT_COLORS.get(color_type, REVENANT_COLORS["default"])


def revenant_title(screen_name: str) -> str:
    return f"REVENANT | {screen_name}"


def standard_footer(text: str = None) -> str:
    suffix = f" | {text}" if text else ""
    return f"REVENANT command interface{suffix}"


def format_section(title: str, lines: list[str]) -> str:
    body = "\n".join(line for line in lines if line)
    return f"**{title}**\n{DIVIDER}\n{body}" if body else f"**{title}**\n{DIVIDER}"


def kv(label: str, value) -> str:
    return f"**{label}:** {value if value not in (None, '') else 'Unknown'}"


def build_revenant_embed(
    title: str,
    description: str,
    color_type: str = "default",
    fields: list[tuple[str, str, bool]] = None,
    image: str = None,
    footer: str = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=revenant_title(title),
        description=description[:4096] if description else None,
        color=get_revenant_color(color_type),
    )
    for field in fields or []:
        if len(field) == 2:
            name, value = field
            inline = False
        else:
            name, value, inline = field
        embed.add_field(name=name, value=(value or "None")[:1024], inline=inline)
    if image:
        embed.set_image(url=image)
    embed.set_footer(text=standard_footer(footer))
    return embed
