"""
===== FILE: main.py (OPTIMIZED) =====
FIXES APPLIED:
1. Flask keep-alive: thêm use_reloader=False, threaded=True, log debug
2. Flask start TRƯỚC khi bot khởi động → Render detect port ngay
3. Logging format chuẩn hơn (timestamp + level)
4. on_command_error: xử lý thêm MissingRequiredArgument, CommandNotFound
5. on_ready: log đầy đủ guild count
6. EXCLUDE_MODULES giữ nguyên (đúng)
7. Thêm health check route /health cho Render uptime monitor
8. Slash command sync: guild sync ngay lập tức + global sync chạy nền
"""

import discord
from discord.ext import commands
import asyncio
import os
import sys
import math
import logging
from flask import Flask
from threading import Thread

# ─── Logging Setup (phải setup TRƯỚC mọi thứ) ───────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────
# Set SERVER_ID trong Environment Variables trên Render (hoặc hardcode nếu cần)
SERVER_ID = int(os.environ.get("SERVER_ID", 0))

# ─── Keep-alive Flask Server ─────────────────────────────────────────────────
_app = Flask(__name__)

_flask_log = logging.getLogger("werkzeug")
_flask_log.setLevel(logging.WARNING)

@_app.route("/")
def _home():
    return "✅ bot is running!", 200

@_app.route("/health")
def _health():
    return {"status": "ok", "bot": str(bot.user) if bot.is_ready() else "starting"}, 200

def _run_server():
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"[Flask] Keep-alive server starting on port {port}")
    _app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )

_flask_thread = Thread(target=_run_server, daemon=True, name="FlaskKeepAlive")
_flask_thread.start()
logger.info("[Main] Flask keep-alive thread started")

# ─── Discord Bot Setup ────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(
    command_prefix=["dtn ", "Dtn ", "d", "dn", "dnt", "D", "Dnt", "Dn"],
    intents=intents,
    owner_id=1146763208011546687,
    help_command=None,
)

# ─── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="reload")
@commands.is_owner()
async def reload_cog(ctx, name: str):
    """Reload a Cog dynamically."""
    try:
        await bot.reload_extension(name)
        await ctx.send(f"✅ Đã reload `{name}.py` thành công!")
        logger.info(f"Reloaded extension: {name}")
    except commands.ExtensionNotLoaded:
        await ctx.send(f"⚠️ `{name}` chưa được load.")
    except commands.ExtensionNotFound:
        await ctx.send(f"❌ Không tìm thấy file `{name}.py`.")
    except Exception as e:
        await ctx.send(f"❌ Lỗi khi reload `{name}`: {e}")
        logger.error(f"Failed to reload {name}: {e}")

@bot.command(name="sync")
@commands.is_owner()
async def sync_commands(ctx):
    """Sync slash commands thủ công (guild + global)."""
    msg = await ctx.send("🔄 Đang sync slash commands...")
    try:
        if SERVER_ID:
            guild_obj = discord.Object(id=SERVER_ID)
            bot.tree.copy_global_to(guild=guild_obj)
            guild_synced = await bot.tree.sync(guild=guild_obj)
            logger.info(f"Manual guild sync: {len(guild_synced)} commands")
        global_synced = await bot.tree.sync()
        logger.info(f"Manual global sync: {len(global_synced)} commands")
        await msg.edit(content=f"✅ Sync xong! Guild: `{len(guild_synced) if SERVER_ID else 'N/A'}` | Global: `{len(global_synced)}`")
    except Exception as e:
        await msg.edit(content=f"❌ Sync thất bại: {e}")
        logger.error(f"Manual sync failed: {e}")

# ─── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    """Bot startup event — sync slash commands."""
    guild_count = len(bot.guilds)
    logger.info(f"Bot logged in as {bot.user} | Guilds: {guild_count}")
    print(f"--- Myonster Bot đã sẵn sàng: {bot.user} | {guild_count} server(s) ---")

    # ── Guild sync (tức thì, dùng cho dev/test) ──────────────────────────────
    if SERVER_ID:
        try:
            guild_obj = discord.Object(id=SERVER_ID)
            # Copy toàn bộ global commands vào guild để test ngay
            bot.tree.copy_global_to(guild=guild_obj)
            guild_synced = await bot.tree.sync(guild=guild_obj)
            logger.info(f"[Sync] Guild sync OK — {len(guild_synced)} slash command(s) → guild {SERVER_ID}")
        except Exception as e:
            logger.error(f"[Sync] Guild sync FAILED: {e}")
    else:
        logger.warning("[Sync] SERVER_ID chưa set — bỏ qua guild sync")

    # ── Global sync (chạy nền, propagate sau ~1h) ─────────────────────────────
    asyncio.create_task(_global_sync())

async def _global_sync():
    """Sync slash commands globally — chạy nền, không block on_ready."""
    try:
        global_synced = await bot.tree.sync()
        logger.info(f"[Sync] Global sync OK — {len(global_synced)} slash command(s)")
    except Exception as e:
        logger.error(f"[Sync] Global sync FAILED: {e}")

@bot.event
async def on_command_error(ctx, error):
    """Global error handler."""

    if hasattr(ctx.command, "on_error"):
        return

    error = getattr(error, "original", error)

    if isinstance(error, commands.CommandOnCooldown):
        remaining = math.ceil(error.retry_after)
        await ctx.send(
            f"⏳ | {ctx.author.mention}, lệnh đang cooldown, còn **{remaining}s** nữa!",
            delete_after=5,
        )

    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            f"❌ | Thiếu tham số: `{error.param.name}`. Dùng lệnh đúng cú pháp nhé!",
            delete_after=8,
        )

    elif isinstance(error, commands.CommandNotFound):
        pass

    elif isinstance(error, commands.MissingPermissions):
        await ctx.send(
            f"🚫 | Bạn không có quyền dùng lệnh này!",
            delete_after=5,
        )

    elif isinstance(error, commands.NotOwner):
        await ctx.send("🚫 | Lệnh này chỉ dành cho chủ bot!", delete_after=5)

    else:
        logger.error(f"Unhandled error in command '{ctx.command}': {type(error).__name__}: {error}")
        raise error

# ─── Extension Loader ─────────────────────────────────────────────────────────

async def load_extensions():
    """
    Load tất cả Cog hợp lệ trong thư mục hiện tại.

    Cogs có slash command: rpg_sell, rpg_game, rpg_weapon, rpg_shop, rpg_crate, rpg_trade
    Các file này sẽ được tự động load cùng với các Cog khác.

    EXCLUDE_MODULES: các module utility thuần túy, KHÔNG phải Cog.
    """

    EXCLUDE_MODULES = {
        "main",       # File này — không phải Cog
        "rpg_core",   # Library: DB I/O + game logic
        "rpg_addon",  # Library: shared utility helpers
        "rpg_item",   # Data module: item definitions
    }

    loaded_count  = 0
    skipped_count = 0
    failed_count  = 0

    for filename in sorted(os.listdir("./")):
        if not filename.endswith(".py"):
            continue

        ext_name = filename[:-3]

        if ext_name in EXCLUDE_MODULES:
            logger.info(f"⏭️  Skipping utility module: {filename}")
            skipped_count += 1
            continue

        try:
            await bot.load_extension(ext_name)
            logger.info(f"✅ Loaded Cog: {filename}")
            loaded_count += 1

        except commands.ExtensionAlreadyLoaded:
            logger.warning(f"⚠️  Already loaded: {filename}")

        except commands.NoEntryPointError:
            logger.info(f"⏭️  No setup() in {filename} — skipping")
            skipped_count += 1

        except commands.ExtensionFailed as e:
            logger.error(
                f"❌ Runtime error in {filename} setup(): "
                f"{type(e.original).__name__}: {e.original}"
            )
            failed_count += 1

        except ModuleNotFoundError as e:
            logger.error(f"❌ Import error loading {filename}: {e}")
            failed_count += 1

        except Exception as e:
            logger.error(f"❌ Unexpected error loading {filename}: {type(e).__name__}: {e}")
            failed_count += 1

    logger.info(
        f"Extension loading complete — "
        f"{loaded_count} loaded, {skipped_count} skipped, {failed_count} failed"
    )
    if failed_count > 0:
        logger.warning(f"⚠️  {failed_count} extension(s) failed to load — xem log ở trên.")

# ─── Main Entry Point ─────────────────────────────────────────────────────────

async def main():
    """Main bot startup."""
    async with bot:
        await load_extensions()

        TOKEN = os.environ.get("DISCORD_TOKEN", "")
        if not TOKEN:
            logger.critical(
                "❌ DISCORD_TOKEN chưa được set! "
                "Thêm vào Environment Variables trên Render."
            )
            sys.exit(1)

        try:
            await bot.start(TOKEN)
        except discord.LoginFailure:
            logger.error("❌ Token không hợp lệ — kiểm tra DISCORD_TOKEN.")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ Lỗi khi khởi động bot: {e}")
            sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot đã dừng (KeyboardInterrupt)")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)
