import discord
from discord.ext import commands
import os
import ast
# ──────────────────────────────────────────────
# Danh sách file COG không cần quét
# ──────────────────────────────────────────────
IGNORED_FILES = {
    "help.py",       # chính file này
    "main.py",
    "bot.py",
    "__init__.py",
    "config.py",
    "utils.py",
    "database.py",
    "rpg_database.py",
    "database_helper.py",
    "rpg_core.py",
    "notification.py"
}

# Thư mục chứa các cog (để trống nếu cùng cấp với bot)
COG_DIRS = [
    ".",
    "cogs",
    "commands",
]


def _extract_cog_class(filepath: str) -> str | None:
    """
    Dùng AST để tìm tên class được add trong hàm setup().
    Trả về tên class nếu tìm thấy, None nếu không.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError):
        return None

    for node in ast.walk(tree):
        # Tìm hàm `async def setup(bot):`
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "setup":
            for stmt in ast.walk(node):
                # Tìm `await bot.add_cog(XxxClass(bot))`
                if isinstance(stmt, ast.Await):
                    call = stmt.value
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "add_cog"
                        and call.args
                        and isinstance(call.args[0], ast.Call)
                        and isinstance(call.args[0].func, ast.Name)
                    ):
                        return call.args[0].func.id
    return None


def _scan_cog_files(base_dir: str) -> list[tuple[str, str]]:
    """
    Quét tất cả file .py trong COG_DIRS, trả về list (filepath, class_name).
    """
    found = []
    for rel_dir in COG_DIRS:
        scan_path = os.path.join(base_dir, rel_dir)
        if not os.path.isdir(scan_path):
            continue
        for fname in sorted(os.listdir(scan_path)):
            if not fname.endswith(".py"):
                continue
            if fname in IGNORED_FILES:
                continue
            fpath = os.path.join(scan_path, fname)
            cls_name = _extract_cog_class(fpath)
            if cls_name:
                found.append((fpath, cls_name))
    return found


# ──────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────
class Help(commands.Cog):
    """Hệ thống help tự động quét cog."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._base_dir = os.path.dirname(os.path.abspath(__file__))

    # ── Lấy prefix hiện tại ────────────────────
    def _prefix(self) -> str:
        p = self.bot.command_prefix
        if callable(p):
            return "!"
        if isinstance(p, (list, tuple)):
            return p[0]
        return p

    # ── Build embed tổng quan ──────────────────
    def _overview_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="📖 Danh sách lệnh",
            description=(
                f"Dùng `{self._prefix()}help <tên_cog>` để xem chi tiết từng nhóm lệnh.\n"
                f"Dùng `{self._prefix()}help <lệnh>` để xem chi tiết một lệnh cụ thể."
            ),
            color=0x5865F2,
        )

        for cog_name, cog in sorted(self.bot.cogs.items()):
            cmds = [
                c for c in cog.get_commands()
                if not c.hidden
            ]
            if not cmds:
                continue
            cmd_list = "  ".join(f"`{c.name}`" for c in cmds)
            embed.add_field(
                name=f"🔹 {cog_name}",
                value=cmd_list or "*(không có lệnh)*",
                inline=False,
            )

        embed.set_footer(text=f"Bot: {self.bot.user.name}" if self.bot.user else "")
        return embed

    # ── Build embed chi tiết cog ───────────────
    def _cog_embed(self, cog: commands.Cog) -> discord.Embed:
        prefix = self._prefix()
        embed = discord.Embed(
            title=f"📂 {type(cog).__name__}",
            description=cog.__doc__ or "*Không có mô tả.*",
            color=0x57F287,
        )
        for cmd in sorted(cog.get_commands(), key=lambda c: c.name):
            if cmd.hidden:
                continue
            usage = f"`{prefix}{cmd.name} {cmd.signature}`".strip()
            embed.add_field(
                name=usage,
                value=cmd.help or "*Không có mô tả.*",
                inline=False,
            )
        return embed

    # ── Build embed chi tiết lệnh ──────────────
    def _cmd_embed(self, cmd: commands.Command) -> discord.Embed:
        prefix = self._prefix()
        embed = discord.Embed(
            title=f"⚙️ {prefix}{cmd.qualified_name}",
            description=cmd.help or "*Không có mô tả.*",
            color=0xFEE75C,
        )
        embed.add_field(name="Cú pháp", value=f"`{prefix}{cmd.name} {cmd.signature}`".strip(), inline=False)
        if cmd.aliases:
            embed.add_field(name="Alias", value="  ".join(f"`{a}`" for a in cmd.aliases), inline=False)
        if isinstance(cmd, commands.Group):
            subs = [f"`{s.name}`" for s in cmd.commands if not s.hidden]
            if subs:
                embed.add_field(name="Lệnh con", value="  ".join(subs), inline=False)
        return embed

    # ──────────────────────────────────────────
    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context, *, query: str = None):
        """Hiển thị trợ giúp. Dùng `help <cog>` hoặc `help <lệnh>`."""

        if query is None:
            await ctx.send(embed=self._overview_embed())
            return

        # 1. Khớp cog (không phân biệt hoa thường)
        for cog_name, cog in self.bot.cogs.items():
            if cog_name.lower() == query.lower():
                await ctx.send(embed=self._cog_embed(cog))
                return

        # 2. Khớp lệnh
        cmd = self.bot.get_command(query)
        if cmd:
            await ctx.send(embed=self._cmd_embed(cmd))
            return

        await ctx.send(
            embed=discord.Embed(
                description=f"❌ Không tìm thấy cog hoặc lệnh `{query}`.",
                color=0xED4245,
            )
        )

    # ──────────────────────────────────────────
    @commands.command(name="coglist", hidden=True)
    @commands.is_owner()
    async def coglist(self, ctx: commands.Context):
        """[Owner] Liệt kê tất cả file cog được phát hiện (kể cả chưa load)."""
        found = _scan_cog_files(self._base_dir)
        if not found:
            await ctx.send("Không tìm thấy file cog nào.")
            return

        lines = []
        for fp, cls in found:
            rel = os.path.relpath(fp, self._base_dir)
            loaded = any(type(c).__name__ == cls for c in self.bot.cogs.values())
            status = "✅" if loaded else "⬜"
            lines.append(f"{status} `{rel}` → **{cls}**")

        embed = discord.Embed(
            title="🔍 Cog phát hiện được",
            description="\n".join(lines),
            color=0x5865F2,
        )
        embed.set_footer(text="✅ = đã load  ⬜ = chưa load")
        await ctx.send(embed=embed)


# ──────────────────────────────────────────────
async def setup(bot: commands.Bot):
    # Xoá lệnh help mặc định nếu có
    bot.remove_command("help")
    await bot.add_cog(Help(bot))
