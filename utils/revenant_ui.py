import discord


DIVIDER = "━━━━━━━━━━━━━━━━━━"

REVENANT_COLORS = {
    "default": 0x1F2A33,
    "info": 0x2D3A45,
    "active": 0x7ED957,
    "success": 0x7ED957,
    "warning": 0xF5A623,
    "danger": 0xE74C3C,
    "error": 0xE74C3C,
    "gm": 0x8B5CF6,
    "admin": 0x52616B,
    "deployment": 0x3498DB,
    "intel": 0x8B5CF6,
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


def progress_bar(current: int, total: int, width: int = 12) -> str:
    current = max(0, int(current or 0))
    total = max(0, int(total or 0))
    if total <= 0:
        return "░" * width
    filled = min(width, round((current / total) * width))
    return "█" * filled + "░" * (width - filled)


def dot_bar(count: int, maximum: int = 3) -> str:
    count = max(0, int(count or 0))
    maximum = max(1, int(maximum or 1))
    shown = min(count, maximum)
    suffix = f" +{count - maximum}" if count > maximum else ""
    return " ".join(["●"] * shown + ["○"] * (maximum - shown)) + suffix


def status_label(status: str) -> str:
    raw = (status or "unknown").strip()
    normalized = raw.lower()
    if normalized in {"open", "active", "deployable", "success", "complete", "completed"}:
        marker = "🟢"
    elif normalized in {"pending", "locked", "paused", "standby"}:
        marker = "🟡"
    elif normalized in {"failed", "failure", "closed", "cancelled", "error"}:
        marker = "🔴"
    else:
        marker = "🔵"
    return f"{marker} {raw.upper()}"


def transmission(text: str) -> str:
    return f"🟢 **Transmission:** {text}"


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
