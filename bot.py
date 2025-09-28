import os, json, logging, datetime, asyncio
from typing import Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("theovsvrose")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
ANNOUNCE_CHANNEL_ID = os.getenv("BOT_ANNOUNCE_CHANNEL_ID")
STORAGE_CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID")  # REQUIRED for message storage

EMOJI_TO_PLAYER = {"🕷": "Theo", "🌹": "Rose"}
ROUND_LIMIT = 12
TIEBREAKER_ROUND = 13

# Marker used to find the storage message reliably
STORE_MARKER = "[TVR-DATA-V1]"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Storage via Discord message ----------------
class MessageStore:
    def __init__(self):
        self.channel_id: Optional[int] = int(STORAGE_CHANNEL_ID) if STORAGE_CHANNEL_ID else None
        self.message_id: Optional[int] = None
        self.cache = None  # keep last loaded dict

    def default_data(self):
        return {
            "state": {
                "game_id": 1, "round": 0, "theo_points": 0, "rose_points": 0,
                "tiebreaker": False, "active": True, "round_open": False, "round_message_id": None
            },
            "log": [],
            "lifetime": {"Theo": {"points": 0, "wins": 0}, "Rose": {"points": 0, "wins": 0}},
        }

    async def ensure_ready(self) -> None:
        """Find or create the pinned storage message in the chosen channel."""
        if not self.channel_id:
            raise RuntimeError("Set STORAGE_CHANNEL_ID env var to the ID of your storage channel.")

        channel = bot.get_channel(self.channel_id) or await bot.fetch_channel(self.channel_id)
        # Try to find an existing pinned storage message
        pins = await channel.pins()
        for m in pins:
            if m.author == bot.user and STORE_MARKER in (m.content or ""):
                self.message_id = m.id
                self.cache = self._parse_from_content(m.content)
                log.info("Found pinned storage message %s", m.id)
                return

        # If not found in pins, scan recent history for our marker
        async for m in channel.history(limit=100):
            if m.author == bot.user and STORE_MARKER in (m.content or ""):
                await m.pin()
                self.message_id = m.id
                self.cache = self._parse_from_content(m.content)
                log.info("Found storage message in history, pinned it: %s", m.id)
                return

        # Create a fresh storage message
        data = self.default_data()
        content = self._render_content(data)
        msg = await channel.send(content)
        try:
            await msg.pin()
        except Exception:
            pass
        self.message_id = msg.id
        self.cache = data
        log.info("Created and pinned new storage message %s", msg.id)

    def _render_content(self, data: dict) -> str:
        # Use a code block so it’s readable in Discord
        return f"{STORE_MARKER}\n```json\n{json.dumps(data, indent=2)}\n```"

    def _parse_from_content(self, content: str) -> dict:
        try:
            start = content.index("```json") + len("```json")
            end = content.rindex("```")
            js = content[start:end].strip()
            return json.loads(js)
        except Exception:
            log.warning("Failed to parse storage message, using defaults.")
            return self.default_data()

    async def load(self) -> dict:
        """Load from message; falls back to cache."""
        if not self.message_id:
            await self.ensure_ready()
        channel = bot.get_channel(self.channel_id) or await bot.fetch_channel(self.channel_id)
        msg = await channel.fetch_message(self.message_id)
        data = self._parse_from_content(msg.content)
        self.cache = data
        return data

    async def save(self, data: dict) -> None:
        """Save by editing the pinned message."""
        if not self.message_id:
            await self.ensure_ready()
        channel = bot.get_channel(self.channel_id) or await bot.fetch_channel(self.channel_id)
        msg = await channel.fetch_message(self.message_id)
        await msg.edit(content=self._render_content(data))
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

async def set_state(game_id:int, round_no:int, theo:int, rose:int, tiebreaker:bool, active:bool, round_open:bool, round_message_id: Optional[int]):
    d = await load_data()
    d["state"] = {
        "game_id": game_id, "round": round_no, "theo_points": theo, "rose_points": rose,
        "tiebreaker": tiebreaker, "active": active, "round_open": round_open, "round_message_id": round_message_id
    }
    await save_data(d)

async def log_reaction(guild_id, channel_id, message_id, user_id, emoji, player, delta, game_id, round_no):
    d = await load_data()
    d["log"].append({
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "guild_id": str(guild_id), "channel_id": str(channel_id), "message_id": str(message_id),
        "user_id": str(user_id), "emoji": emoji, "player": player, "delta": delta,
        "game_id": game_id, "round": round_no
    })
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
        log.error(f"Failed to announce: {e}")

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
        await msg.add_reaction("🕷")
        await msg.add_reaction("🌹")
    except Exception as e:
        log.error(f"Failed to add reactions: {e}")

    # open this round
    game_id, round_no_prev, theo, rose, tbreak, active, _, _ = await get_state()
    await set_state(game_id, round_no, theo, rose, tiebreaker, True, True, msg.id)
    return msg

# ---------------- Events / errors ----------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        text = f"⚠️ Command error: `{type(error).__name__}` — see bot console."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except Exception:
        pass
    log.exception("Slash command error: %s", error)

@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            await bot.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            log.info("Synced commands to guild %s", GUILD_ID)
        else:
            await bot.tree.sync()
            log.info("Synced global commands")
    except Exception as e:
        log.error(f"Command sync failed: {e}")
    # Prepare the storage message
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
    d = await load_data()
    lt = d["lifetime"]
    theo_lp = lt.get("Theo", {}).get("points", 0)
    rose_lp = lt.get("Rose", {}).get("points", 0)
    theo_lw = lt.get("Theo", {}).get("wins", 0)
    rose_lw = lt.get("Rose", {}).get("wins", 0)

    embed = discord.Embed(title="TheoVsRose 🎴 — Score", timestamp=datetime.datetime.utcnow())
    embed.add_field(name="Current Game", value=f"Theo 🕷: **{theo}**\nRose 🌹: **{rose}**\nRounds: **{theo+rose}**", inline=False)
    embed.add_field(name="Lifetime", value=f"Theo 🕷 — {theo_lp} pts | {theo_lw} wins\nRose 🌹 — {rose_lp} pts | {rose_lw} wins", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(description="Show lifetime standings")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction):
    d = await load_data()
    lt = d["lifetime"]
    theo_lp = lt.get("Theo", {}).get("points", 0)
    rose_lp = lt.get("Rose", {}).get("points", 0)
    theo_lw = lt.get("Theo", {}).get("wins", 0)
    rose_lw = lt.get("Rose", {}).get("wins", 0)

    embed = discord.Embed(title="TheoVsRose 🎴 — Lifetime Leaderboard", timestamp=datetime.datetime.utcnow())
    embed.add_field(name="Theo 🕷", value=f"{theo_lp} pts | {theo_lw} wins", inline=True)
    embed.add_field(name="Rose 🌹", value=f"{rose_lp} pts | {rose_lw} wins", inline=True)
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

    # Decide where to announce
    announce_channel = None
    if ANNOUNCE_CHANNEL_ID:
        ch = channel.guild.get_channel(int(ANNOUNCE_CHANNEL_ID))
        if ch:
            announce_channel = ch
    if not announce_channel:
        announce_channel = channel

    over, winner, need_tie = is_game_over(theo, rose)

    if need_tie:
        await announce(announce_channel, "🔥 Tie detected! Round 13 — React now to decide the winner! 🔥")
        await post_round_prompt(announce_channel, TIEBREAKER_ROUND, tiebreaker=True)
        return

    if over and winner:
        await update_lifetime(winner, delta_points=0, delta_wins=1)
        await announce(announce_channel, f"🏆 **{winner}** wins the game! Final score: Theo {theo} — Rose {rose}")
        await set_state(game_id + 1, 0, 0, 0, False, True, False, None)
        await post_round_prompt(announce_channel, 1, tiebreaker=False)
        return

    # Otherwise start next round immediately
    next_no = next_round_number(theo, rose)
    await post_round_prompt(announce_channel, next_no, tiebreaker=False)

@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    try:
        if user.bot:
            return
        # Load state
        game_id, round_no, theo, rose, tiebreaker, active, round_open, round_msg_id = await get_state()
        if not is_current_round_message(reaction.message, round_open, round_msg_id):
            return  # ignore old/other messages
        emoji = str(reaction.emoji)
        if emoji not in EMOJI_TO_PLAYER:
            return

        player = EMOJI_TO_PLAYER[emoji]
        # Apply +1 and lock immediately
        if player == "Theo":
            theo += 1
        else:
            rose += 1

        await update_lifetime(player, delta_points=1, delta_wins=0)
        await set_state(game_id, round_no, theo, rose, tiebreaker, True, False, round_m
