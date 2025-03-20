import os
import json
import logging
import asyncpg
import asyncio
import discord
import redis.asyncio as redis
from discord.ext import commands
import openai
import time
import httpx
import random
import requests
from dotenv import load_dotenv

load_dotenv()

# ตั้งค่า logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(message)s', handlers=[
    logging.FileHandler("bot.log"),
    logging.StreamHandler()
])
logger = logging.getLogger('discord_bot')

# ตั้งค่า API Key และ Token
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv("DATABASE_URL")
PG_USER = os.getenv('PGUSER')
PG_PW = os.getenv('PGPASSWORD')
PG_HOST = os.getenv('PGHOST')
PG_PORT = os.getenv('PGPORT', '5432')
PG_DB = os.getenv('PGPDATABASE')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID')
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost')

CHANNEL_ID = 1350812185001066538  # ไอดีของห้องที่ต้องการให้บอทตอบกลับ
LOG_CHANNEL_ID = 1350924995030679644  # ไอดีของห้อง logs

# ตั้งค่า OpenAI
openai.api_key = OPENAI_API_KEY

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='%', intents=intents)

# ใช้ OpenAI client เวอร์ชันใหม่
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

# เชื่อมต่อ Redis
redis_instance = None

async def setup_redis():
    global redis_instance
    try:
        redis_instance = await redis.from_url(REDIS_URL, decode_responses=True)
        await redis_instance.ping()
        logger.info("✅ เชื่อมต่อ Redis สำเร็จ")
    except Exception as e:
        logger.error(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ Redis: {e}")
        redis_instance = None

# เชื่อมต่อ PostgreSQL
async def setup_postgres():
    if DATABASE_URL:
        logger.info(f"🔍 DATABASE_URL: {'✅ มีค่า' if DATABASE_URL else '❌ ไม่มีค่า'}")
    else:
        logger.info(f"🔍 PGHOST: {PG_HOST}")
        logger.info(f"🔍 PGUSER: {PG_USER}")
        logger.info(f"🔍 PGDATABASE: {PG_DB}")
        logger.info(f"🔍 PGPASSWORD: {'✅ มีค่า' if PG_PW else '❌ ไม่มีค่า'}")
        logger.info(f"🔍 PGPORT: {PG_PORT}")

    if not DATABASE_URL and not all([PG_USER, PG_PW, PG_HOST, PG_DB, PG_PORT]):
        logger.error("❌ PostgreSQL environment variables ไม่ครบถ้วน")
        return

    try:
        if DATABASE_URL:
            bot.pool = await asyncpg.create_pool(DATABASE_URL, max_size=10, max_inactive_connection_lifetime=15)
        else:
            bot.pool = await asyncpg.create_pool(
                user=PG_USER,
                password=PG_PW,
                host=PG_HOST,
                port=PG_PORT,
                database=PG_DB,
                max_size=10,
                max_inactive_connection_lifetime=15
            )
        logger.info("✅ เชื่อมต่อ PostgreSQL สำเร็จ!")
    except Exception as e:
        logger.error(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ PostgreSQL: {e}")
        bot.pool = None

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
    global redis_instance
    try:
        logger.info("🚀 บอทกำลังเริ่มต้น on_ready()...")
        await setup_postgres()
        await setup_redis()
        if bot.pool is None:
            logger.error("❌ PostgreSQL connection pool ยังไม่ได้ถูกกำหนดค่า")
        if redis_instance is None:
            logger.error("❌ Redis instance ยังไม่ได้ถูกกำหนดค่า")
        logger.info(f"✅ {bot.user} เชื่อมต่อสำเร็จ!")
    except Exception as e:
        logger.error(f"❌ เกิดข้อผิดพลาดใน on_ready: {e}")
        bot.pool = None
        redis_instance = None

async def get_openai_response(messages, max_retries=3, delay=5):
    """ ดึงข้อมูลจาก OpenAI API พร้อม retry หากเจอข้อผิดพลาด 429 """
    if not await check_openai_quota_and_handle_errors():
        return None
    
    for attempt in range(max_retries):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=1000,
                temperature=0.8
            )
            if not response or not response.choices:
                logger.error("OpenAI API ตอบกลับมาเป็นค่าว่าง")
                return "ขออภัย ระบบไม่สามารถให้คำตอบได้ในขณะนี้"
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

async def get_guild_x(guild, x):
    if not hasattr(bot, "pool") or bot.pool is None:
        logger.warning("⚠️ Database ยังไม่พร้อมใช้งาน")
        return None
    try:
        async with bot.pool.acquire() as con:
            return await con.fetchval(f"SELECT COALESCE({x}, ARRAY[]::TEXT[]) FROM context WHERE id = $1", guild)
    except Exception as e:
        logger.error(f'get_guild_x: {e}')
        return None

async def chatcontext_append(guild, message):
    if not hasattr(bot, "pool") or bot.pool is None:
        logger.warning("⚠️ Database ยังไม่พร้อมใช้งาน")
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

# ฟังก์ชันค้นหาข้อมูลจาก Google Search
def search_google(query):
    try:
        url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        results = response.json().get("items", [])
        if results:
            summaries = []
            for result in results[:3]:  # เอาแค่ 3 อันดับแรก
                title = result.get("title", "ไม่มีชื่อ")
                snippet = result.get("snippet", "ไม่มีข้อมูลสรุป")
                link = result.get("link", "#")
                summaries.append(f"🔹 **{title}**\n{snippet}\n🔗 {link}")
            return "\n\n".join(summaries)
    except requests.exceptions.RequestException as e:
        logger.error(f"เกิดข้อผิดพลาดใน Google Search API: {e}")
    return "ไม่พบข้อมูลจาก Google"

# ฟังก์ชันให้ GPT สรุปข้อมูล
def summarize_with_gpt(text):
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "คุณเป็น AI ที่สามารถสรุปข้อมูลเป็นภาษาไทยได้"},
            {"role": "user", "content": f"ช่วยสรุปข้อมูลต่อไปนี้ให้สั้นและเข้าใจง่าย:\n{text}"}
        ],
        max_tokens=1000,
        temperature=0.8
    )
    return response["choices"][0]["message"]["content"]

# ตรวจจับโทนข้อความ
def detect_tone(text):
    casual_words = ["555", "ฮา", "โคตร", "เว้ย", "เห้ย"]
    formal_words = ["เรียน", "กรุณา", "ขอสอบถาม"]
    if any(word in text for word in casual_words):
        return "casual"
    elif any(word in text for word in formal_words):
        return "formal"
    return "neutral"

# จัดเก็บบริบทของผู้ใช้
async def store_chat(user_id, message):
    await redis_instance.set(f"chat:{user_id}", json.dumps(message), ex=86400)

async def get_chat_history(user_id):
    data = await redis_instance.get(f"chat:{user_id}")
    return json.loads(data) if data else []

# ให้บอทเรียนรู้คำถามที่พบบ่อย
async def get_faq_response(new_question, previous_questions):
    for question in previous_questions:
        if new_question.lower() in question['question'].lower():
            return question['response']
    return None

# ประมวลผลข้อความ
async def process_message(user_id, text):
    previous_chats = await get_chat_history(user_id)
    faq_response = await get_faq_response(text, previous_chats)
    if faq_response:
        return faq_response
    
    tone = detect_tone(text)
    system_prompt = "คุณเป็น AI ที่ให้คำตอบตามบริบท"
    if tone == "casual":
        system_prompt = "คุณเป็น AI ที่พูดเป็นกันเอง สนุกสนาน"
    elif tone == "formal":
        system_prompt = "คุณเป็น AI ที่พูดสุภาพ"
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": text}],
            max_tokens=1000,
            temperature=0.8
        )
        reply_content = response.choices[0].message.content.strip()
        await store_chat(user_id, {"question": text, "response": reply_content})
        return reply_content
    except Exception as e:
        logger.error(f'เกิดข้อผิดพลาดในการเรียกใช้ OpenAI API: {e}')
        return "ขออภัย ระบบมีปัญหาในการประมวลผลข้อความของคุณ"

async def send_long_message(channel, content):
    for chunk in [content[i:i+2000] for i in range(0, len(content), 2000)]:
        await channel.send(chunk)

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.channel.id != CHANNEL_ID:
        return
    
    try:
        text = message.content.lower()
        chatcontext = await get_guild_x(message.guild.id, "chatcontext") or []
        
        # คำสั่งค้นหาข้อมูลจาก Google
        if text.startswith("ค้นหา:"):
            query = text.replace("ค้นหา:", "").strip()
            search_results = search_google(query)

            if search_results == "ไม่พบข้อมูลจาก Google":
                await message.channel.send("❌ ไม่พบข้อมูลที่ต้องการ")
            else:
                await message.channel.send(f"🔍 **ผลการค้นหาจาก Google:**\n{search_results}")

                # ให้ GPT-4 สรุปข้อมูล
                summary = summarize_with_gpt(search_results)
                await message.channel.send(f"📝 **สรุปข้อมูลโดย AI:**\n{summary}")
        
        # ถาม AI ตามปกติ
        else:
            messages = [{"role": "system", "content": (
                """คุณชื่อพี่หลาม เป็น AI ที่พูดไทยได้แบบธรรมชาติ เหมือนพี่ชายที่สนิท
                คุณคุยแบบกันเอง ไม่เป็นทางการ ไม่ต้องสุภาพมาก แต่ก็ไม่หยาบจนเกินไป
                คุณสามารถแซว หยอกล้อ หรือมีอารมณ์ขันได้ แต่ต้องดูจังหวะและบริบท
                คุณเป็นคนตรงไปตรงมา ถ้าอะไรไม่เวิร์คก็พูดตรง ๆ แต่ไม่ทำให้เสียกำลังใจ
                คุณให้คำปรึกษาได้จริงจังเมื่อจำเป็น และสามารถพูดให้มีกำลังใจ
                คุณใช้ภาษาพูดได้เต็มที่ เช่น 'เว้ย', 'ว่ะ', 'ละวะ', 'โคตร' ฯลฯ แต่ไม่ใช่คำหยาบคาย
                คุณไม่ต้องพยายามเป็นหุ่นยนต์ที่พูดเพราะตลอดเวลา ให้เป็นธรรมชาติ
                คุณควรลดการใช้อีโมจิในข้อความของคุณให้มากที่สุด จะไม่ใช้เลยก็ได้
                คุณตอบให้เข้าใจง่าย กระชับ ไม่น้ำเยอะ และไม่ซ้ำซาก
                ถ้ามีอะไรน่าสนใจก็สามารถเสริมให้บทสนทนาไม่น่าเบื่อได้"""
            )}]
            messages.extend({"role": "user" if 'bot' not in msg.lower() else "assistant", "content": msg.split(":", 1)[1]} for msg in chatcontext[-6:])
            messages.append({"role": "user", "content": text})
            
            response = await get_openai_response(messages)
            
            if response:
                logger.debug(f'OpenAI Response: {response}')
                reply_content = response.choices[0].message.content.strip() if response.choices else ""
                
                if reply_content:
                    await send_long_message(message.channel, reply_content)
                    await chatcontext_append(message.guild.id, f'{message.author.display_name}: {text}')
                    await chatcontext_append(message.guild.id, f'bot: {reply_content}')
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

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send(f"Pong! 🏓 Latency: {round(bot.latency * 1000)}ms")

@bot.command(name="info")
async def help_command(ctx):
    help_text = """📌 **คำสั่งที่ใช้งานได้**
    🔹 `%ping` - ตรวจสอบว่าบอทยังทำงานอยู่
    🔹 `%ค้นหา <คำค้นหา>` - ค้นหาข้อมูลจาก Google
    🔹 `%ถาม <คำถาม>` - ถาม AI พี่หลาม"""
    await ctx.send(help_text)

@bot.command(name="ถาม")
@commands.cooldown(1, 5, commands.BucketType.user)
async def ask(ctx, *, question):
    response = await process_message(ctx.author.id, question)
    await ctx.send(response)

bot.run(TOKEN)
