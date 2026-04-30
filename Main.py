"""
===== FILE: main.py (FIXED) =====
FIXES APPLIED:
1. EXCLUDE_MODULES now only contains confirmed non-Cog utility modules:
   rpg_core, rpg_addon, rpg_item (+ main itself).
   rpg_weapon, rpg_quest, rpg_daily, cash, exp are NOT excluded —
   they are valid Cogs and must be attempted.

2. Removed the name-prefix filter (startswith('rpg_') / == 'cash').
   That filter silently dropped exp.py and any future Cog with an
   unexpected name. A module is a Cog if and only if it has
   `async def setup(bot)` — detected at runtime via NoEntryPointError.

3. Kept comprehensive per-extension try/except logging:
   success / NoEntryPointError / ExtensionFailed / ModuleNotFoundError / fallback.
"""

import discord
from discord.ext import commands
import asyncio
import os
import sys
import math
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(
    command_prefix=["dtn ", "Dtn ", "d", "dn", "dnt", "D", "Dnt", "Dn"],
    intents=intents,
    owner_id=1146763208011546687,
    help_command=None
)

@bot.command(name="reload")
@commands.is_owner()
async def reload_cog(ctx, name: str):
    """Reload a Cog dynamically."""
    try:
        await bot.reload_extension(name)
        await ctx.send(f"✅ Đã cập nhật file `{name}.py` thành công!")
        logger.info(f"Reloaded extension: {name}")
    except Exception as e:
        await ctx.send(f"❌ Lỗi khi cập nhật `{name}`: {e}")
        logger.error(f"Failed to reload {name}: {e}")
# ✅ Global error handling
@bot.event
async def on_command_error(ctx, error):
    """Handle command errors globally."""
    if isinstance(error, commands.CommandOnCooldown):
        remaining = math.ceil(error.retry_after)
        await ctx.send(
            f"⏳ | {ctx.author.mention}, lệnh đang cooldown, còn **{remaining}s** nữa!",
            delete_after=3  # 👈 tự xoá sau 3 giây
        )
    else:
        logger.error(f"Command error in {ctx.command}: {error}")
        raise error
@bot.event
async def on_ready():
    """Bot startup event."""
    print(f"--- Myonster Bot đã sẵn sàng: {bot.user.name} ---")
    logger.info(f"Bot logged in as {bot.user}")

async def load_extensions():
    """
    Load ALL valid Cog files found in the current directory.

    A file is a valid Cog if and only if it defines `async def setup(bot)`.
    We do NOT use filenames or prefixes to judge this — discord.py will
    raise NoEntryPointError for any file that lacks setup(), and we log
    that as an informational skip rather than an error.

    Hard-excluded (confirmed non-Cog utility modules with no setup()):
        main      – this file
        rpg_core  – database + core game logic library
        rpg_addon – shared utility / helper functions
        rpg_item  – item-definition data module

    Everything else (cash, exp, rpg_weapon, rpg_enchant, rpg_catch, …)
    is attempted.  If it has setup() it loads; if not, NoEntryPointError
    tells us cleanly.
    """

    # Only confirmed non-Cog modules — the absolute minimum exclusion list.
    # DO NOT add cash, exp, rpg_weapon, rpg_quest, rpg_daily, etc. here.
    EXCLUDE_MODULES = {
        'main',       # This file — never a Cog
        'rpg_core',   # Pure library: DB I/O + game logic, no setup()
        'rpg_addon',  # Pure library: shared utility helpers, no setup()
        'rpg_item',   # Pure data module: item definitions, no setup()
    }

    loaded_count  = 0
    skipped_count = 0
    failed_count  = 0

    for filename in sorted(os.listdir('./')):         # sorted for stable log order
        if not filename.endswith('.py'):
            continue

        ext_name = filename[:-3]  # strip .py

        # Skip confirmed non-Cog utility modules
        if ext_name in EXCLUDE_MODULES:
            logger.info(f"⏭️  Skipping utility module: {filename}")
            skipped_count += 1
            continue

        # --- Attempt to load every other .py file ---
        try:
            await bot.load_extension(ext_name)
            logger.info(f"✅ Loaded Cog:  {filename}")
            loaded_count += 1

        except commands.ExtensionAlreadyLoaded:
            logger.warning(f"⚠️  Already loaded: {filename}")

        except commands.NoEntryPointError:
            # File exists but has no async def setup(bot) — not a Cog, safe to skip.
            logger.info(f"⏭️  No setup() in {filename} — not a Cog, skipping")
            skipped_count += 1

        except commands.ExtensionFailed as e:
            # setup() raised an exception at runtime
            logger.error(
                f"❌ Runtime error in {filename} setup(): "
                f"{type(e.original).__name__}: {e.original}"
            )
            failed_count += 1

        except ModuleNotFoundError as e:
            # The module itself (or one of its imports) could not be found
            logger.error(f"❌ Import error loading {filename}: {e}")
            failed_count += 1

        except Exception as e:
            logger.error(
                f"❌ Unexpected error loading {filename}: "
                f"{type(e).__name__}: {e}"
            )
            failed_count += 1

    # ── Summary ────────────────────────────────────────────────────────────────
    logger.info(
        f"Extension loading complete — "
        f"{loaded_count} loaded, {skipped_count} skipped, {failed_count} failed"
    )
    if failed_count > 0:
        logger.warning(
            f"⚠️  {failed_count} extension(s) failed to load. "
            f"Review the errors above."
        )

async def main():
    """Main bot startup."""
    async with bot:
        await load_extensions()

        # ⚠️  SECURITY: Move your token to an environment variable or a .env file.
        #     e.g.  TOKEN = os.environ["DISCORD_TOKEN"]
        TOKEN = os.environ.get("DISCORD_TOKEN", "")
        if not TOKEN:
            logger.critical(
                "DISCORD_TOKEN environment variable is not set. "
                "Set it before starting the bot."
            )
            sys.exit(1)

        try:
            await bot.start(TOKEN)
        except discord.LoginFailure:
            logger.error("Invalid bot token — check DISCORD_TOKEN.")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)
                         
