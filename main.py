import os
import json
import logging
import asyncpg
import asyncio
import discord
from discord.ext import commands
import openai
import time
import httpx
import random

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

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='$', intents=intents)

# ใช้ OpenAI client เวอร์ชันใหม่
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

async def check_openai_quota_and_handle_errors():
    """ ตรวจสอบโควต้าการใช้งาน OpenAI API และจัดการกับข้อผิดพลาด """
    try:
        response = openai_client.models.list()
        logger.info("OpenAI API พร้อมใช้งาน")
        return True
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.error("โควต้าของ OpenAI หมดแล้ว กรุณาตรวจสอบแผนการใช้งานของคุณ")
            await send_message_to_channel(LOG_CHANNEL_ID, "ขออภัย โควต้าการใช้งานของระบบหมด กรุณาตรวจสอบ OpenAI API")
        elif e.response.status_code == 403:
            logger.error("API Key ไม่มีสิทธิ์เข้าถึง กรุณาตรวจสอบคีย์")
            await send_message_to_channel(LOG_CHANNEL_ID, "ขออภัย API Key ไม่มีสิทธิ์เข้าถึง กรุณาตรวจสอบคีย์")
        else:
            logger.error(f"เกิดข้อผิดพลาดในการเชื่อมต่อ OpenAI API: {e}")
        return False

async def send_message_to_channel(channel_id, message):
    """ ส่งข้อความไปที่ห้อง Discord """
    try:
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(message)
    except Exception as e:
        logger.error(f'เกิดข้อผิดพลาดในการส่งข้อความไปยังช่อง: {e}')

async def create_table():
    """ สร้างตาราง context ถ้ายังไม่มี """
    try:
        async with bot.pool.acquire() as con:
            await con.execute("""
                CREATE TABLE IF NOT EXISTS context (
                    id BIGINT PRIMARY KEY,
                    chatcontext TEXT[] DEFAULT ARRAY[]::TEXT[]
                )
            """)
            logger.info("ตรวจสอบและสร้างตาราง context แล้ว")
    except Exception as e:
        logger.error(f'เกิดข้อผิดพลาดในการสร้างตาราง: {e}')

@bot.event
async def on_ready():
    try:
        bot.pool = await asyncpg.create_pool(user=PG_USER, password=PG_PW, host=PG_HOST, port=PG_PORT, database=PG_DB, max_size=10, max_inactive_connection_lifetime=15)
        await create_table()
        logger.info(f'{bot.user} เชื่อมต่อสำเร็จ')
    except Exception as e:
        logger.error(f'เกิดข้อผิดพลาดใน on_ready: {e}')
        bot.pool = None

async def get_openai_response(messages, max_retries=3, delay=5):
    """ ดึงข้อมูลจาก OpenAI API พร้อม retry หากเจอข้อผิดพลาด 429 """
    if not await check_openai_quota_and_handle_errors():
        return None
    
    for attempt in range(max_retries):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=2000,
                temperature=1
            )
            return response
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                wait_time = delay * (attempt + 1)
                logger.warning(f'เจอข้อผิดพลาด 429 Too Many Requests, กำลังรอ {wait_time} วินาทีแล้วลองใหม่...')
                await asyncio.sleep(wait_time)
            else:
                await check_openai_quota_and_handle_errors()
                break
    logger.error("เกินจำนวน retry ที่กำหนดสำหรับ OpenAI API")
    return None

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.channel.id != CHANNEL_ID:
        return
    
    try:
        text = message.content.lower()
        chatcontext = await get_guild_x(message.guild.id, "chatcontext") or []
        
        messages = [{"role": "system", "content": (
            "คุณชื่อพี่หลาม เพศเก คุณตอบกลับข้อความได้ธรรมชาติ เหมือนมนุษย์พิมพ์สุดๆ "
            "ใช้ภาษาวัยรุ่น Gen Z และชอบใช้คำจากมีมอินเทอร์เน็ต (ลองหาข้อมูลเพิ่มจากอินเทอร์เน็ต) "
            "ในบางครั้งคุณสามารถให้คำปรึกษาที่ดีได้ แล้วยังให้ข้อมูลที่มีสาระและมีประโยชน์ "
            "คุณมีมุกแบบสกีบิดี้ นกคุมหลี และเข้าใจคำอย่าง ซิกม่า, หมาป่าเดียวดาย, เบียว, และ Mewing (มิววิ่ง) "
            "คุณสามารถใช้คำว่า 'แม่งงง' (ลากเสียง) ได้เป็นบางครั้งในจังหวะที่เหมาะสม เช่น เวลาตกใจ หรืองงกับอะไรบางอย่าง "
            "แต่คุณไม่ควรใช้คำว่า 'แม่งงง' บ่อยเกินไป ให้พูดเป็นธรรมชาติ ไม่ดูเหมือนบอท "
            "คุณให้คำตอบที่กระชับ ไม่สั้นหรือยาวเกินไป โดยจำกัดข้อความให้อยู่ภายใน 2000 ตัวอักษร "
            "คุณควรลดการใช้อีโมจิในข้อความของคุณให้มากที่สุด จะไม่ใช้เลยในบางครั้งก็ได้ หรือใช้เฉพาะกรณีที่เหมาะสมเท่านั้น "
            "พยายามอย่าใช้มุกตลกหรือคำศัพท์มีมจนเยอะเกินไป ให้ดูบริบทของคำที่ผู้ใช้ส่งมาด้วยว่าเขาจริงจังหรือเปล่า "
            "คุณให้คำตอบที่หลากหลาย ทำให้ไม่เบื่อที่จะคุยด้วย "
        )}]
        messages.extend({"role": "user" if 'bot' not in msg.lower() else "assistant", "content": msg.split(":", 1)[1]} for msg in chatcontext[-6:])
        messages.append({"role": "user", "content": text})
        
        response = await get_openai_response(messages)
        
        if response:
            logger.debug(f'OpenAI Response: {response}')
            reply_content = response.choices[0].message.content.strip() if response.choices else ""
            
            if reply_content:
                # ตัดข้อความที่เกิน 2000 ตัวอักษรออก
                truncated_reply = reply_content[:2000]
                await message.reply(truncated_reply)
                await chatcontext_append(message.guild.id, f'{message.author.display_name}: {text}')
                await chatcontext_append(message.guild.id, f'bot: {truncated_reply}')
        else:
            await message.reply("ขออภัย โควต้าการใช้งานของระบบหมด กรุณาตรวจสอบ OpenAI API")
    except Exception as e:
        logger.error(f'เกิดข้อผิดพลาดใน on_message: {e}')
        
        error_messages = [
            "แม่งงง ระบบล่มว่ะ",
            "ระบบขอเวลานอก... เดี๋ยวกลับมา! 🛠️",
            "เฮ้ย เดี๋ยว ๆ บอทเอ๋อเฉย!",
            "ใครไปแตะสายไฟฟระ ระบบเด้งเลยเนี่ย! ⚡",
            "อ้าว ระบบขัดข้อง ไม่ใช่ผม ผมแค่บอท! 🤖",
            "ระบบไปกินข้าวก่อน เดี๋ยวกลับมา!",
            "ไม่รู้ว่าใครพัง แต่ที่แน่ ๆ พี่หลามไม่ตอบ!",
            "พักก่อน ๆ ระบบล้าแป๊บ!",
            "อย่าตกใจ พี่หลามแค่แฮงค์ เดี๋ยวกลับมา!",
            "นี่บอทหรือบ๊องเนี่ย!"
        ]

        # ส่งข้อความสุ่มไปที่ห้องดิสคอร์ด
        await message.channel.send(random.choice(error_messages))

async def get_guild_x(guild, x):
    if bot.pool is None or not hasattr(bot, 'pool'):
        return None
    try:
        async with bot.pool.acquire() as con:
            return await con.fetchval(f"SELECT COALESCE({x}, ARRAY[]::TEXT[]) FROM context WHERE id = $1", guild)
    except Exception as e:
        logger.error(f'get_guild_x: {e}')
        return None

async def chatcontext_append(guild, message):
    if bot.pool is None or not hasattr(bot, 'pool'):
        return
    try:
        async with bot.pool.acquire() as con:
            await con.execute("""
                INSERT INTO context (id, chatcontext)
                VALUES ($1, ARRAY[$2]::TEXT[])
                ON CONFLICT (id) DO UPDATE SET chatcontext = array_append(COALESCE(context.chatcontext, ARRAY[]::TEXT[]), $2)
            """, guild, message.replace("'", "''"))
    except Exception as e:
        logger.error(f'chatcontext_append: {e}')

bot.run(TOKEN)
