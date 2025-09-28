import os, json, logging, datetime
from typing import Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
ANNOUNCE_CHANNEL_ID = os.getenv("BOT_ANNOUNCE_CHANNEL_ID")
DATA_PATH = os.getenv("DATA_PATH", "data.json")

EMOJI_TO_PLAYER = {"🕷": "Theo", "🌹": "Rose"}
ROUND_LIMIT = 12
TIEBREAKER_ROUND = 13

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- local storage ----------------
def _default_data():
    return {
        "state": {
            "game_id": 1,
            "round": 0,
            "theo_points": 0,
            "rose_points": 0,
            "tiebreaker": False,
            "active": True,
            "round_open": False,
            "round_message_id": None,
        },
        "log": [],
        "lifetime": {"Theo": {"points": 0, "wins": 0}, "Rose": {"points": 0, "wins": 0}},
    }

def load_data():
    if not os.path.exists(DATA_PATH):
        d = _default_data()
        save_data(d)
        return d
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        d = json.load(f)
    # Backward-compatible defaults
    st = d.setdefault("state", {})
    st.setdefault("game_id", 1)
    st.setdefault("round", 0)
    st.setdefault("theo_points", 0)
    st.setdefault("rose_points", 0)
    st.setdefault("tiebreaker", False)
    st.setdefault("active", True)
    st.setdefault("round_open", False)
    st.setdefault("round_message_id", None)
    d.setdefault("log", [])
    d.setdefault("lifetime", {"Theo": {"points": 0, "wins": 0}, "Rose": {"points": 0, "wins": 0}})
    return d

def save_data(data):
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_PATH)

def get_state():
    st = load_data()["state"]
    return (
        st["game_id"],
        st["round"],
        st["theo_points"],
        st["rose_points"],
        st["tiebreaker"],
        st["active"],
        st["round_open"],
        st["round_message_id"],
    )

def set_state(game_id:int, round_no:int, theo:int, rose:int, tiebreaker:bool, active:bool,
              round_open:bool, round_message_id: Optional[int]):
    d = load_data()
    d["state"] = {
        "game_id": game_id,
        "round": round_no,
        "theo_points": theo,
        "rose_points": rose,
        "tiebreaker": tiebreaker,
        "active": active,
        "round_open": round_open,
        "round_message_id": round_message_id,
    }
    save_data(d)

def log_reaction(guild_id, channel_id, message_id, user_id, emoji, player, delta, game_id, round_no):
    d = load_data()
    d["log"].append({
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "message_id": str(message_id),
        "user_id": str(user_id),
        "emoji": emoji,
        "player": player,
        "delta": delta,
        "game_id": game_id,
        "round": round_no
    })
    save_data(d)

def update_lifetime(player:str, delta_points:int=0, delta_wins:int=0):
    d = load_data()
    lt = d["lifetime"]
    if player not in lt:
        lt[player] = {"points": 0, "wins": 0}
    lt[player]["points"] += int(delta_points)
    lt[player]["wins"] += int(delta_wins)
    save_data(d)

async def announce(channel: discord.abc.Messageable, content: str):
    try:
        await channel.send(content)
    except Exception as e:
        logging.error(f"Failed to announce: {e}")

# ---------------- game logic ----------------
def next_round_number(theo_pts:int, rose_pts:int) -> int:
    return theo_pts + rose_pts + 1

def is_game_over(theo_pts:int, rose_pts:int):
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
        logging.error(f"Failed to add reactions: {e}")
    # open this round
    game_id, round_no_prev, theo, rose, tbreak, active, _, _ = get_state()
    set_state(game_id, round_no, theo, rose, tiebreaker, True, True, msg.id)
    return msg

# ---------------- events / errors ----------------
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
    logging.exception("Slash command error: %s", error)

@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            await bot.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            logging.info("Synced commands to guild %s", GUILD_ID)
        else:
            await bot.tree.sync()
            logging.info("Synced global commands")
    except Exception as e:
        logging.error(f"Command sync failed: {e}")
    logging.info("Logged in as %s", bot.user)

def admin_only():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)

# ---------------- commands ----------------
@bot.tree.command(description="Ping test")
@app_commands.guild_only()
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! ✅", ephemeral=True)

@bot.tree.command(description="Post a Round Winner prompt")
@app_commands.guild_only()
async def round(interaction: discord.Interaction):
    game_id, round_no, theo, rose, tiebreaker, active, round_open, round_msg_id = get_state()
    if not active:
        set_state(game_id, 0, 0, 0, False, True, False, None)
    new_round = next_round_number(theo, rose)
    await interaction.response.send_message(f"Starting Round {new_round}…", ephemeral=True)
    await post_round_prompt(interaction, new_round, tiebreaker=False)

@bot.tree.command(description="Show current game and lifetime standings")
@app_commands.guild_only()
async def score(interaction: discord.I
