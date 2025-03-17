import os
import json
import logging
import asyncpg
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import openai
import time
import httpx
import random
from googletrans import Translator  # ใช้ Google Translate API
import wikipediaapi  # ใช้ Wikipedia API

# ตั้งค่า logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s', handlers=[
    logging.FileHandler("bot.log"),
    logging.StreamHandler()
])
logger = logging.getLogger('discord_bot')

# ตั้งค่า API Key และ Token
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TOKEN = os.getenv('DISCORD_TOKEN')
PG_USER = os.getenv('PGUSER')
PG_PW = os.getenv('PGPASSWORD')
PG_HOST = os.getenv('PGHOST')
PG_PORT = os.getenv('PGPORT')
PG_DB = os.getenv('PGPDATABASE')

CHANNEL_ID = 1350812185001066538  # ไอดีของห้องที่ต้องการให้บอทตอบกลับ
LOG_CHANNEL_ID = 1350924995030679644  # ไอดีของห้อง logs

# ตั้งค่า Wikipedia API พร้อม User-Agent
user_agent = "my-app-name/1.0 (https://example.com/contact)"
wikipedia = wikipediaapi.Wikipedia("th", headers={"User-Agent": user_agent})

# ตั้งค่า Discord Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='$', intents=intents)
translator = Translator()

# ใช้ OpenAI client เวอร์ชันใหม่
openai.api_key = OPENAI_API_KEY

# --------- SLASH COMMANDS ---------
@bot.tree.command(name="help", description="แสดงรายการคำสั่งที่ใช้งานได้")
async def slash_help(interaction: discord.Interaction):
    help_text = """
    **คำสั่งที่ใช้งานได้:**
    - `$help` → แสดงรายการคำสั่ง
    - `$ask <คำถาม>` → ถาม AI โดยไม่ใช้บริบทเก่า
    - `$clear` → ล้างบริบทของคุณ
    - `$setrole <บทบาท>` → ตั้งบทบาทของ AI
    - `$translate <lang> <ข้อความ>` → แปลข้อความ (เช่น `$translate en สวัสดี`)
    - `$search <คำค้นหา>` → ค้นหาข้อมูลจาก Wikipedia
    """
    await interaction.response.send_message(help_text, ephemeral=True)

# --------- PREFIX COMMANDS ---------
@bot.command()
async def help(ctx):
    await ctx.send("ใช้ `/help` เพื่อดูคำสั่งที่ใช้งานได้")

@bot.command()
async def ask(ctx, *, question):
    response = await get_openai_response([{"role": "user", "content": question}])
    if response:
        await ctx.send(response.choices[0].message.content.strip())
    else:
        await ctx.send("ไม่สามารถดึงข้อมูลจาก AI ได้")

@bot.command()
async def clear(ctx):
    await update_user_context(ctx.guild.id, ctx.author.id, clear=True)
    await ctx.send("ล้างบริบทสนทนาเรียบร้อย!")

@bot.command()
async def setrole(ctx, *, role):
    await update_user_role(ctx.guild.id, ctx.author.id, role)
    await ctx.send(f"ตั้งบทบาทของ AI เป็น: {role}")

@bot.command()
async def translate(ctx, lang, *, text):
    translated = translator.translate(text, dest=lang)
    await ctx.send(f"แปล ({lang}): {translated.text}")

@bot.command()
async def search(ctx, *, query):
    page = wikipedia.page(query)
    if page.exists():
        summary = page.summary[:1000] + "..."
        await ctx.send(f"**{page.title}**\n{summary}\n{page.fullurl}")
    else:
        await ctx.send("ไม่พบข้อมูลใน Wikipedia")

# --------- ฟังก์ชันจัดการ Context ---------
async def update_user_context(guild_id, user_id, message=None, clear=False):
    if bot.pool is None:
        return
    async with bot.pool.acquire() as con:
        if clear:
            await con.execute("DELETE FROM context WHERE id = $1 AND user_id = $2", guild_id, user_id)
        else:
            context = await con.fetchval("SELECT chatcontext FROM context WHERE id = $1 AND user_id = $2", guild_id, user_id) or []
            if len(context) >= 30:
                context.pop(0)
            context.append(message)
            await con.execute("INSERT INTO context (id, user_id, chatcontext) VALUES ($1, $2, $3) ON CONFLICT (id, user_id) DO UPDATE SET chatcontext = $3", guild_id, user_id, context)

async def update_user_role(guild_id, user_id, role):
    if bot.pool is None:
        return
    async with bot.pool.acquire() as con:
        await con.execute("INSERT INTO context (id, user_id, role) VALUES ($1, $2, $3) ON CONFLICT (id, user_id) DO UPDATE SET role = $3", guild_id, user_id, role)

# --------- รันบอท ---------
@bot.event
async def on_ready():
    bot.pool = await asyncpg.create_pool(user=PG_USER, password=PG_PW, host=PG_HOST, port=PG_PORT, database=PG_DB)
    await bot.tree.sync()
    logger.info(f'{bot.user} เชื่อมต่อสำเร็จ')

bot.run(TOKEN)
