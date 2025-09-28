import sys; sys.modules["audioop"] = None  # avoid audioop import on slim runtimes
import os, json, logging, datetime, asyncio
from typing import Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web  # for Render Web health check

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("theovsvrose")

# ---- Env ----
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
ANNOUNCE_CHANNEL_ID = os.getenv("BOT_ANNOUNCE_CHANNEL_ID")
STORAGE_CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID")  # numeric channel ID

# ---- Game constants ----
EMOJI_TO_PLAYER = {"🕷": "Theo", "🌹": "Rose"}
ROUND_LIMIT = 12
TIEBREAKER_ROUND = 13
STORE_MARKER = "[TVR-DATA-V2]"  # bump version to migrate from old format

# ---- Intents ----
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ===================== Storage via pinned Discord message (compact) =====================
# Compact schema saved to Discord message (single line, small keys):
# {"s":{"gid":1,"r":0,"tp":0,"rp":0,"tb":false,"a":true,"ro":false,"rm":null},
#  "lt":{"Theo":{"p":0,"w":0},"Rose":{"p":0,"w":0}}}
#
# Internal (expanded) schema we use in code:
# state: {game_id, round, theo_points, rose_points, tiebreaker, active, round_open, round_message_id}
# lifetime: {Theo:{points,wins}, Rose:{points,wins}}

def default_internal():
    return {
        "state": {
            "game_id": 1, "round": 0, "theo_points": 0, "rose_points": 0,
            "tiebreaker": False, "active": True, "round_open": False, "round_message_id": None
        },
        "lifetime": {"Theo": {"points": 0, "wins": 0}, "Rose": {"points": 0, "wins": 0}},
    }

def to_compact(internal: dict) -> dict:
    st = internal["state"]
    lt = internal["lifetime"]
    return {
        "s": {
            "gid": st["game_id"], "r": st["round"], "tp": st["theo_points"], "rp": st["rose_points"],
            "tb": st["tiebreaker"], "a": st["active"], "ro": st["round_open"], "rm": st["round_message_id"],
        },
        "lt": {
            "Theo": {"p": lt.get("Theo", {}).get("points", 0), "w": lt.get("Theo", {}).get("wins", 0)},
            "Rose": {"p": lt.get("Rose", {}).get("points", 0), "w": lt.get("Rose", {}).get("wins", 0)},
        },
    }

def to_internal(compact: dict) -> dict:
    # accept either compact or previous expanded formats
    if "s" in compact and "lt" in compact:  # compact
        s = compact["s"]; lt = compact["lt"]
        return {
            "state": {
                "game_id": s.get("gid", 1), "round": s.get("r", 0),
                "theo_points": s.get("tp", 0), "rose_points": s.get("rp", 0),
                "tiebreaker": s.get("tb", False), "active": s.get("a", True),
                "round_open": s.get("ro", False), "round_message_id": s.get("rm", None),
            },
            "lifetime": {
                "Theo": {"points": lt.get("Theo", {}).get("p", 0), "wins": lt.get("Theo", {}).get("w", 0)},
                "Rose": {"points": lt.get("Rose", {}).get("p", 0), "wins": lt.get("Rose", {}).get("w", 0)},
            },
        }
    # fallback: expanded (V1)
    d = default_internal()
    try:
        st = compact.get("state", {})
        d["state"].update({
            "game_id": st.get("game_id", 1), "round": st.get("round", 0),
            "theo_points": st.get("theo_points", 0), "rose_points": st.get("rose_points", 0),
            "tiebreaker": st.get("tiebreaker", False), "active": st.get("active", True),
            "round_open": st.get("round_open", False), "round_message_id": st.get("round_message_id", None),
        })
        lt = compact.get("lifetime", {})
        d["lifetime"]["Theo"]["points"] = lt.get("Theo", {}).get("points", 0)
        d["lifetime"]["Theo"]["wins"]   = lt.get("Theo", {}).get("wins", 0)
        d["lifetime"]["Rose"]["points"] = lt.get("Rose", {}).get("points", 0)
        d["lifetime"]["Rose"]["wins"]   = lt.get("Rose", {}).get("wins", 0)
    except Exception:
        pass
    return d

class MessageStore:
    def __init__(self):
        self.channel_id: Optional[int] = int(STORAGE_CHANNEL_ID) if STORAGE_CHANNEL_ID else None
        self.message_id: Optional[int] = None
        self.cache = None

    async def ensure_ready(self) -> None:
        if not self.channel_id:
            raise RuntimeError("Set STORAGE_CHANNEL_ID to a numeric channel ID.")
        channel = bot.get_channel(self.channel_id) or await bot.fetch_channel(self.channel_id)
        # Look for a pinned message with our marker
        pins = await channel.pins()
        for m in pins:
            if m.author == bot.user and STORE_MARKER in (m.content or ""):
                self.message_id = m.id
                self.cache = self._parse(m.content)
                log.info("Found pinned storage message %s", m.id)
                return
        # Search recent history
        async for m in channel.history(limit=100):
            if m.author == bot.user and STORE_MARKER in (m.content or ""):
                try: await m.pin()
                except Exception: pass
                self.message_id = m.id
                self.cache = self._parse(m.content)
                log.info("Found storage in history; pinned %s", m.id)
                return
        # Create fresh compact message
        data = default_internal()
        content = self._render(data)
        msg = await channel.send(content)
        try: await msg.pin()
        except Exception: pass
        self.message_id = msg.id
        self.cache = data
        log.info("Created storage message %s", msg.id)

    def _render(self, internal: dict) -> str:
        compact = to_compact(internal)
        # One line; no code block; keep it small
        payload = json.dumps(compact, separators=(",", ":"))
        # Include marker so we can find it again
        content = f"{STORE_MARKER} {payload}"
        # Safety: ensure under 2000 chars
        if len(content) > 1900:
            # If we ever get close (unlikely now), drop any optional bits (none currently)
            pass
        return content

    def _parse(self, content: str) -> dict:
        try:
            # Expect "[TVR-DATA-V2] {json...}"
            idx = content.index("{")
            js = content[idx:]
            comp = json.loads(js)
            return to_internal(comp)
        except Exception:
            log.warning("Parse failed; falling back to defaults.")
            return default_internal()

    async def load(self) -> dict:
        if not self.message_id:
            await self.ensure_ready()
        channel = bot.get_channel(self.channel_id) or await bot.fetch_channel(self.channel_id)
        msg = await channel.fetch_message(self.message_id)
        data = self._parse(msg.content)
        self.cache = data
        return data

    async def save(self, data: dict) -> None:
        if not self.message_id:
            await self.ensure_ready()
        channel = bot.get_channel(self.channel_id) or await bot.fetch_channel(self.channel_id)
        msg = await channel.fetch_message(self.message_id)
        await msg.edit(content=self._render(data))
        self.cache = data

store = MessageStore()

# ---------------- Helpers over storage ----------------
async def load_data() -> dict:
    return await store.load()

async def save_data(data: dict) -> None:
    await store.save(data)

async def get_state():
    st = (await load_data())["state"]
    return (
        st["game_id"], st["round"], st["theo_points"], st["rose_points"],
        st["tiebreaker"], st["active"], st["round_open"], st["round_message_id"]
    )

async def set_state(game_id:int, round_no:int, theo:int, rose:int, tiebreaker:bool, active:bool,
                    round_open:bool, round_message_id: Optional[int]):
    d = await load_data()
    d["state"] = {
        "game_id": game_id, "round": round_no, "theo_points": theo, "rose_points": rose,
        "tiebreaker": tiebreaker, "active": active, "round_open": round_open, "round_message_id": round_message_id
    }
    await save_data(d)

async def update_lifetime(player:str, delta_points:int=0, delta_wins:int=0):
    d = await load_data()
    lt = d["lifetime"]
    if player not in lt:
        lt[player] = {"points": 0, "wins": 0}
    lt[player]["points"] += int(delta_points)
    lt[player]["wins"] += int(delta_wins)
    await save_data(d)

async def announce(channel: discord.abc.Messageable, content: str):
    try:
        await channel.send(content)
    except Exception as e:
        log.error(f"Announce failed: {e}")

# ---------------- Game logic ----------------
def next_round_number(theo_pts:int, rose_pts:int) -> int:
    return theo_pts + rose_pts + 1

def is_game_over(theo_pts:int, rose_pts:int) -> Tuple[bool, Optional[str], bool]:
    total = theo_pts + rose_pts
    if total < ROUND_LIMIT:
        return False, None, False
    if total == ROUND_LIMIT and theo_pts == rose_pts:
        return False, None, True
    if total >= ROUND_LIMIT and theo_pts != rose_pts:
        winner = "Theo" if theo_pts > rose_pts else "Rose"
        return True, winner, False
    return False, None, False

async def post_round_prompt(target, round_no:int, tiebreaker:bool=False):
    content = f"**Round {round_no} Winner — React below with 🕷 for Theo or 🌹 for Rose!**"
    if tiebreaker:
        content = "🔥 **Tie detected! Round 13 — React now to decide the winner!** 🔥\n" + content
    channel = target.channel if isinstance(target, discord.Interaction) else target
    msg = await channel.send(content)
    try:
        await msg.add_reaction("🕷"); await msg.add_reaction("🌹")
    except Exception as e:
        log.error(f"Add reactions failed: {e}")
    game_id, _, theo, rose, tbreak, active, _, _ = await get_state()
    await set_state(game_id, round_no, theo, rose, tiebreaker, True, True, msg.id)
    return msg

# ---------------- Health server (for Render Web) ----------------
async def _health(request): return web.Response(text="OK")
async def start_web():
    app = web.Application()
    app.add_routes([web.get("/", _health)])
    port = int(os.getenv("PORT", "10000"))
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port); await site.start()
    log.info("Health server listening on %s", port)

# ---------------- Events / errors ----------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        text = f"⚠️ Command error: `{type(error).__name__}` — see logs."
        if interaction.response.is_done(): await interaction.followup.send(text, ephemeral=True)
        else: await interaction.response.send_message(text, ephemeral=True)
    except Exception: pass
    log.exception("Slash command error: %s", error)

@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            await bot.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            log.info("Synced commands to guild %s", GUILD_ID)
        else:
            await bot.tree.sync(); log.info("Synced global commands")
    except Exception as e:
        log.error("Command sync failed: %s", e)
    try:
        await store.ensure_ready()
    except Exception as e:
        log.error("Storage init failed: %s", e)
    log.info("Logged in as %s", bot.user)

def admin_only():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)

# ---------------- Commands ----------------
@bot.tree.command(description="Ping test")
@app_commands.guild_only()
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! ✅", ephemeral=True)

@bot.tree.command(description="Post a Round Winner prompt")
@app_commands.guild_only()
async def round(interaction: discord.Interaction):
    game_id, round_no, theo, rose, tiebreaker, active, round_open, round_msg_id = await get_state()
    if not active:
        await set_state(game_id, 0, 0, 0, False, True, False, None)
    new_round = next_round_number(theo, rose)
    await interaction.response.send_message(f"Starting Round {new_round}…", ephemeral=True)
    await post_round_prompt(interaction, new_round, tiebreaker=False)

@bot.tree.command(description="Show current game and lifetime standings")
@app_commands.guild_only()
async def score(interaction: discord.Interaction):
    game_id, round_no, theo, rose, tiebreaker, active, _, _ = await get_state()
    d = await load_data(); lt = d["lifetime"]
    embed = discord.Embed(title="TheoVsRose 🎴 — Score", timestamp=datetime.datetime.utcnow())
    embed.add_field(name="Current Game", value=f"Theo 🕷: **{theo}**\nRose 🌹: **{rose}**\nRounds: **{theo+rose}**", inline=False)
    embed.add_field(
        name="Lifetime",
        value=f"Theo 🕷 — {lt['Theo']['points']} pts | {lt['Theo']['wins']} wins\n"
              f"Rose 🌹 — {lt['Rose']['points']} pts | {lt['Rose']['wins']} wins",
        inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(description="Show lifetime standings")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction):
    d = await load_data(); lt = d["lifetime"]
    embed = discord.Embed(title="TheoVsRose 🎴 — Lifetime Leaderboard", timestamp=datetime.datetime.utcnow())
    embed.add_field(name="Theo 🕷", value=f"{lt['Theo']['points']} pts | {lt['Theo']['wins']} wins", inline=True)
    embed.add_field(name="Rose 🌹", value=f"{lt['Rose']['points']} pts | {lt['Rose']['wins']} wins", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(description="Show lifetime standings (alias)")
@app_commands.guild_only()
async def lifetime(interaction: discord.Interaction):
    await leaderboard.callback(interaction)

@bot.tree.command(description="Reset the current game (Admin only)")
@app_commands.guild_only()
@admin_only()
async def reset(interaction: discord.Interaction):
    game_id, *_ = (await get_state())[0:1]
    await set_state(game_id, 0, 0, 0, False, True, False, None)
    await interaction.response.send_message("Current game has been reset to 0–0.", ephemeral=True)

@bot.tree.command(description="Set the default announcement channel (Admin only)")
@app_commands.guild_only()
@admin_only()
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    global ANNOUNCE_CHANNEL_ID
    ANNOUNCE_CHANNEL_ID = str(channel.id)
    await interaction.response.send_message(f"Announcements will be posted in {channel.mention}.", ephemeral=True)

# ---------------- Reactions (auto-advance on first valid reaction) ----------------
def is_current_round_message(msg: discord.Message, round_open: bool, round_message_id: Optional[int]):
    return round_open and round_message_id and msg.id == round_message_id and msg.author == bot.user

async def close_round_message(msg: discord.Message):
    try:
        if "Winner" in msg.content and "**Closed**" not in msg.content:
            await msg.edit(content=msg.content + "\n\n✅ **Closed** — counting first reaction only.")
        await msg.clear_reactions()
    except Exception:
        pass

async def after_score_and_flow(channel: discord.TextChannel):
    game_id, round_no, theo, rose, tiebreaker, active, round_open, round_msg_id = await get_state()
    announce_channel = None
    if ANNOUNCE_CHANNEL_ID:
        ch = channel.guild.get_channel(int(ANNOUNCE_CHANNEL_ID))
        if ch: announce_channel = ch
    if not announce_channel: announce_channel = channel

    over, winner, need_tie = is_game_over(theo, rose)
    if need_tie:
        await announce(announce_channel, "🔥 Tie detected! Round 13 — React now to decide the winner! 🔥")
        await post_round_prompt(announce_channel, TIEBREAKER_ROUND, tiebreaker=True); return
    if over and winner:
        await update_lifetime(winner, delta_points=0, delta_wins=1)
        await announce(announce_channel, f"🏆 **{winner}** wins the game! Final score: Theo {theo} — Rose {rose}")
        await set_state(game_id + 1, 0, 0, 0, False, True, False, None)
        await post_round_prompt(announce_channel, 1, tiebreaker=False); return
    next_no = next_round_number(theo, rose)
    await post_round_prompt(announce_channel, next_no, tiebreaker=False)

@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    try:
        if user.bot: return
        game_id, round_no, theo, rose, tiebreaker, active, round_open, round_msg_id = await get_state()
        if not is_current_round_message(reaction.message, round_open, round_msg_id): return
        emoji = str(reaction.emoji)
        if emoji not in EMOJI_TO_PLAYER: return
        player = EMOJI_TO_PLAYER[emoji]
        if player == "Theo": theo += 1
        else: rose += 1
        await update_lifetime(player, delta_points=1, delta_wins=0)
        await set_state(game_id, round_no, theo, rose, tiebreaker, True, False, round_msg_id)
        await close_round_message(reaction.message)
        await after_score_and_flow(reaction.message.channel)
    except Exception as e:
        log.exception("on_reaction_add error: %s", e)

@bot.event
async def on_reaction_remove(reaction: discord.Reaction, user: discord.User):
    return  # ignored in auto-advance mode

# ---------------- Main ----------------
if __name__ == "__main__":
    if not TOKEN:
        print("Set DISCORD_TOKEN env var.")
    elif not STORAGE_CHANNEL_ID:
        print("Set STORAGE_CHANNEL_ID env var (numeric channel ID).")
    else:
        async def main():
            # start the small web server so Render Web stays up
            await start_web()
            # start the Discord bot
            await bot.start(TOKEN)
        asyncio.run(main())

