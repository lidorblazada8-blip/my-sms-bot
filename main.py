import discord
from discord import ui, app_commands
from discord.ext import commands, tasks
import os
import asyncio
import random
import re
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, db

# ==========================================
# --- 1. הגדרות קבועות, רולים וטוקן ---
# ==========================================
TOKEN = os.getenv('DISCORD_TOKEN')
FIREBASE_URL = os.getenv('FIREBASE_URL')

MY_USER_ID = 1130542850883469443
ADMIN_ACCESS_ROLE_ID = 1499868525844627478

# רשימת ה-IDs המורשים לעקוף את כל החסימות והפקודות (Owners)
OWNER_IDS = [1493293951959044147, 1130542850883469443, 1483411120961093642]

# הגדרות שרת הגיבוי
BACKUP_GUILD_ID = 1533515085455032440  
BACKUP_INVITE_URL = "https://discord.gg/ZZan9bXPnV"

CHANNELS = {
    "FEEDBACK": 1505632543066689686,        
    "WELCOME": 1505634203776319539,         
    "SUGGESTIONS": 1505636914588287006,     
    "REPORTS": 1505636914588287006,         
    "ANTI_ALT": 1505637562633420870,        
    "OWNER_LOGS": 1505637562633420870,      
    "WARNS_LOG": 1502014872655888554,
    "BOOST": 1505632369674158241             
}

ROLES = {
    "SUPPORTER": 1503819239310627068, 
    "VIP": 1503817695466881255,
    "TICKET_STAFF": 1501316672345211041, 
    "MUTE": 1501953906736103535,
    "VERIFIED": 1501316672345211041
}

feedback_cooldown = {} 
spam_tracker = {}
join_tracker = []
raid_mode_active = False

leaderboard_config = {"channel_id": None, "message_id": None}

# ==========================================
# --- 2. חיבור ל-Firebase Realtime Database ---
# ==========================================
try:
    cred = credentials.Certificate(os.getenv('FIREBASE_CONFIG')) if os.getenv('FIREBASE_CONFIG') else credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_URL
    })
    print("✅ החיבור ל-Firebase עבר בהצלחה!")
except Exception as e:
    print(f"⚠️ אזהרת חיבור ל-Firebase: {e}")

def get_user_data(user_id, key, default_value=0):
    try:
        ref = db.reference(f'users/{user_id}/{key}')
        val = ref.get()
        return val if val is not None else default_value
    except:
        return default_value

def set_user_data(user_id, key, value):
    try:
        ref = db.reference(f'users/{user_id}/{key}')
        ref.set(value)
    except:
        pass

def get_all_balances():
    try:
        ref = db.reference('users')
        users = ref.get()
        if not users: return {}
        return {int(uid): data.get('balance', 0) for uid, data in users.items() if 'balance' in data}
    except:
        return {}

def has_owner_or_admin_permission(i: discord.Interaction):
    if i.user.id in OWNER_IDS: return True
    admin_role = i.guild.get_role(ADMIN_ACCESS_ROLE_ID)
    return admin_role in i.user.roles

async def send_unauthorized_alert(i: discord.Interaction, cmd_name: str, required_role: str = "גישה מוגבלת"):
    log_ch = i.guild.get_channel(CHANNELS["OWNER_LOGS"])
    if log_ch:
        emb = discord.Embed(title="🚨 ניסיון פריצה / שימוש אסור בפקודה!", color=0xff0000, timestamp=datetime.now())
        emb.add_field(name="המשתמש החשוד:", value=f"{i.user.mention} ({i.user.name})", inline=True)
        emb.add_field(name="הפקודה שנחסמה:", value=f"`/{cmd_name}`", inline=True)
        emb.add_field(name="הרשאה נדרשת:", value=f"`{required_role}`", inline=False)
        await log_ch.send(embed=emb)
    try:
        dm_emb = discord.Embed(title="⚠️ אזהרת מערכת חמורה - Cyber Security", description=f"שלום {i.user.name},\nניסית להפעיל את הפקודה המוגנת `/{cmd_name}` ללא הרשאות המתאימות.", color=0xff0000)
        await i.user.send(embed=dm_emb)
    except: pass

# ==========================================
# --- 3. חלונות קופצים (Modals) ---
# ==========================================
class SuggestionModal(ui.Modal, title="💡 הצעה חדשה לשיפור"):
    suggestion = ui.TextInput(label="מה ההצעה שלך?", style=discord.TextStyle.paragraph)
    
    async def on_submit(self, i: discord.Interaction):
        emb = discord.Embed(title="💡 הצעה חדשה", description=self.suggestion.value, color=0xffd700, timestamp=datetime.now())
        emb.set_author(name=i.user.name, icon_url=i.user.display_avatar.url)
        msg = await i.guild.get_channel(CHANNELS["SUGGESTIONS"]).send(embed=emb)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        await i.response.send_message("✅ ההצעה שלך נשלחה בהצלחה!", ephemeral=True)

class ReportModal(ui.Modal, title="🚨 דיווח על שחקן"):
    player = ui.TextInput(label="שם או ID של השחקן המדווח", placeholder="דוגמה: User#1111")
    reason = ui.TextInput(label="סיבת הדיווח ופרטים", style=discord.TextStyle.paragraph)
    
    async def on_submit(self, i: discord.Interaction):
        emb = discord.Embed(title="🚨 דיווח חדש התקבל", color=0xff0000, timestamp=datetime.now())
        emb.add_field(name="המדווח:", value=i.user.mention, inline=True)
        emb.add_field(name="הנידון:", value=self.player.value, inline=True)
        emb.add_field(name="סיבה:", value=self.reason.value, inline=False)
        await i.guild.get_channel(CHANNELS["REPORTS"]).send(embed=emb)
        await i.response.send_message("✅ הדיווח הועבר לטיפול צוות הניהול.", ephemeral=True)

class FeedbackModal(ui.Modal, title="📩 שליחת פידבק לשרת"):
    content = ui.TextInput(label="רשום את הפידבק שלך", style=discord.TextStyle.paragraph)
    anon = ui.TextInput(label="האם לשלוח כאנונימי? (כן/לא)", max_length=2, default="לא")
    
    async def on_submit(self, i: discord.Interaction):
        now = datetime.now()
        if i.user.id in feedback_cooldown and now < feedback_cooldown[i.user.id] + timedelta(minutes=5):
            return await i.response.send_message("❌ יש להמתין 5 דקות בין פידבק לפידבק!", ephemeral=True)
        
        emb = discord.Embed(title="✨ פידבק חדש מהקהילה", description=self.content.value, color=0x00ffff, timestamp=now)
        emb.set_footer(text=f"נשלח על ידי: {i.user.name if self.anon.value != 'כן' else 'אנונימי'}")
        
        inline_view = ui.View(timeout=None)
        b_inline = ui.Button(label="📩 שלח פידבק", style=discord.ButtonStyle.primary, custom_id="f_open_inline")
        b_inline.callback = lambda inter: inter.response.send_modal(FeedbackModal())
        inline_view.add_item(b_inline)
        
        await i.guild.get_channel(CHANNELS["FEEDBACK"]).send(embed=emb, view=inline_view)
        
        admin_logs_ch = i.guild.get_channel(1505636914588287006)
        if admin_logs_ch:
            admin_emb = discord.Embed(title="🕵️ לוג פידבק חסוי לצוות", description=self.content.value, color=0x7289da, timestamp=now)
            admin_emb.add_field(name="המשתמש האמיתי:", value=f"{i.user.mention} (`{i.user.id}`)", inline=True)
            admin_emb.add_field(name="נשלח כאנונימי לקהילה?", value=f"`{self.anon.value}`", inline=True)
            await admin_logs_ch.send(embed=admin_emb)
            
        feedback_cooldown[i.user.id] = now
        await i.response.send_message("✅ הפידבק נשלח בהצלחה, תודה לך!", ephemeral=True)

# ==========================================
# --- 4. פאנלים קבועים ומערכת כפתורים ---
# ==========================================
class VerifyView(ui.View):
    def __init__(self): super().__init__(timeout=None)
        
    @ui.button(label="✅ לחץ כאן לאימות", style=discord.ButtonStyle.success, custom_id="v_verify")
    async def verify_user(self, i: discord.Interaction, b: ui.Button):
        role = i.guild.get_role(ROLES["VERIFIED"])
        if role in i.user.roles: 
            return await i.response.send_message("❌ אתה כבר מאומת בשרת!", ephemeral=True)
            
        backup_guild = i.client.get_guild(BACKUP_GUILD_ID)
        if not backup_guild:
            return await i.response.send_message("⚠️ שגיאה: הבוט אינו נמצא בשרת הגיבוי או שה-ID שהוזן שגוי. פנה לאונר.", ephemeral=True)
            
        backup_member = backup_guild.get_member(i.user.id)
        if not backup_member:
            try:
                backup_member = await backup_guild.fetch_member(i.user.id)
            except discord.NotFound:
                backup_member = None
            except Exception:
                backup_member = None

        if not backup_member:
            emb = discord.Embed(
                title="🔒 אימות נכשל - חסר שלב חובה!",
                description=f"שלום {i.user.mention},\nבשביל להשלים את תהליך האימות בשרת, **אתה חייב להיות חבר גם בשרת הגיבוי שלנו**.\n\nאנא כנס לשרת מהכפתור למטה ולאחר מכן לחץ שוב על כפתור האימות!",
                color=0xff0000,
                timestamp=datetime.now()
            )
            
            link_view = ui.View()
            link_view.add_item(ui.Button(label="🔗 כניסה לשרת הגיבוי", url=BACKUP_INVITE_URL))
            return await i.response.send_message(embed=emb, view=link_view, ephemeral=True)
            
        await i.user.add_roles(role)
        await i.response.send_message("🎉 אומתת בהצלחה (נמצאת גם בשרת הגיבוי)! ברוך הבא לשרת.", ephemeral=True)

# ==========================================
# --- 5. קלאס הבוט ומערכת אבטחה מורחבת ---
# ==========================================
class CyberMasterBot(commands.Bot):
    def __init__(self): 
        super().__init__(command_prefix="!", intents=discord.Intents.all())
        self.invites = {} 

    async def setup_hook(self):
        self.add_view(VerifyView())
        
        inline_view = ui.View(timeout=None)
        b_inline = ui.Button(label="📩 שלח פידבק", style=discord.ButtonStyle.primary, custom_id="f_open_inline")
        b_inline.callback = lambda inter: inter.response.send_modal(FeedbackModal())
        inline_view.add_item(b_inline)
        self.add_view(inline_view)
        
        # --- 🔒 הגנה גלובלית הרמטית על כל פקודות ה-Slash בשרת ---
        @self.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            if isinstance(error, app_commands.CheckFailure):
                await interaction.response.send_message("❌ פקודה חסומה! רק מי שמחזיק ברול <@&1499868525844627478> מורשה להשתמש בבוט זה.", ephemeral=True)
                await send_unauthorized_alert(interaction, interaction.command.name, "רול גישה מורשה")
            else:
                print(f"Error: {error}")

        @self.tree.interaction_check
        async def global_interaction_check(interaction: discord.Interaction) -> bool:
            if interaction.type in (discord.InteractionType.component, discord.InteractionType.modal_submit):
                return True
                
            if interaction.user.id in OWNER_IDS:
                return True
                
            admin_role = interaction.guild.get_role(ADMIN_ACCESS_ROLE_ID)
            if admin_role and admin_role in interaction.user.roles:
                return True
                
            return False

        self.lb_loop.start()
        await self.tree.sync()

    @tasks.loop(minutes=5)
    async def lb_loop(self):
        if leaderboard_config["channel_id"] and leaderboard_config["message_id"]:
            ch = self.get_channel(leaderboard_config["channel_id"])
            if ch:
                try:
                    msg = await ch.fetch_message(leaderboard_config["message_id"])
                    emb = discord.Embed(title="🏆 טבלת עשירים - Cyber Economy", color=0xffd700, timestamp=datetime.now())
                    all_bals = get_all_balances()
                    sorted_users = sorted(all_bals.items(), key=lambda x: x[1], reverse=True)[:10]
                    emb.description = "\n".join([f"**#{idx+1}** <@{uid}>  get ₪{bal:,}" for idx, (uid, bal) in enumerate(sorted_users)]) if sorted_users else "אין מידע עדיין."
                    await msg.edit(embed=emb)
                except: pass

bot = CyberMasterBot()

# --- 🚨 מערכות סינון והגנה לצ'אט (אנטי-ספאם ואנטי-לינק) ---
@bot.event
async def on_message(message: discord.Message):
    # 🛑 חסימה מוחלטת ומיידית לבוט הריידר (מוחק ועוצר בלי להמשיך לכלום)
    if message.author.id == 1510282879706333194:
        try: 
            await message.delete()
        except: 
            pass
        return

    if message.author.bot or not message.guild: return
    
    now = datetime.now()
    user_id = message.author.id

    # --- 🔒 מערכת ANTI-LINK מורחבת והרמטית (כולל חסימת רווחים ומעקפים) ---
    if user_id not in OWNER_IDS:
        cleaned_content = re.sub(r'[\s\_\-\*\\\/\.\,\?\!\|\]\[\)\(\}\:\;\d]+', '', message.content).lower()
        bypass_patterns = ["discordgg", "http", "https", "www", ".gg", "discord.com/invite"]
        normal_link_pattern = r"(https?://[^\s]+|discord\.gg/[^\s]+|www\.[^\s]+)"
        
        is_link_detected = re.search(normal_link_pattern, message.content, re.IGNORECASE) or any(p in cleaned_content for p in bypass_patterns)

        if is_link_detected:
            try: 
                await message.delete()  
            except: 
                pass

            current_warns = get_user_data(user_id, 'warns', 0) + 1
            set_user_data(user_id, 'warns', current_warns)

            owner_ch = message.guild.get_channel(CHANNELS["OWNER_LOGS"])
            if owner_ch:
                emb_owner = discord.Embed(title="🚨 מערכת אנטי-קישורים זיהתה איום / מעקף!", color=0xff0000, timestamp=now)
                emb_owner.add_field(name="המשתמש ששלח:", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
                emb_owner.add_field(name="הערוץ שבו נשלח:", value=message.channel.mention, inline=True)
                emb_owner.add_field(name="תוכן ההודעה שנחסמה:", value=f"```\n{message.content}\n```", inline=False)
                emb_owner.add_field(name="סטטוס אזהרות נוכחי:", value=f"`{current_warns}/3`", inline=False)
                await owner_ch.send(embed=emb_owner)

            warn_ch = message.guild.get_channel(CHANNELS["WARNS_LOG"])
            if warn_ch:
                await warn_ch.send(f"⚠️ **אזהרה אוטומטית** | המשתמש {message.author.mention} הוזהר על ידי מערכת האבטחה. סיבה: `פרסום קישורים או ניסיון מעקף` (`{current_warns}/3`)")
            
            if current_warns >= 3:
                mute_role = message.guild.get_role(ROLES["MUTE"])
                if mute_role: 
                    await message.author.add_roles(mute_role)
                
            return  

    if message.author.id in OWNER_IDS or message.author.guild_permissions.administrator:
        await bot.process_commands(message)
        return

    # --- 🚨 מערכת ANTI-SPAM ---
    if user_id not in spam_tracker: spam_tracker[user_id] = []
    spam_tracker[user_id].append(now)
    spam_tracker[user_id] = [t for t in spam_tracker[user_id] if now - t < timedelta(seconds=3)]
    
    if len(spam_tracker[user_id]) > 5:
        spam_tracker[user_id] = []
        try: await message.channel.purge(limit=5, check=lambda m: m.author.id == user_id)
        except: pass
        
        mute_role = message.guild.get_role(ROLES["MUTE"])
        if mute_role: await message.author.add_roles(mute_role)
            
        warn_ch = message.guild.get_channel(CHANNELS["WARNS_LOG"])
        if warn_ch:
            emb = discord.Embed(title="🚨 מערכת אנטי-ספאם הופעלה!", color=0xff0000, timestamp=now)
            emb.description = f"המשתמש {message.author.mention} ספים בצ'אט והושתק אוטומטית לשמירה על השרת."
            await warn_ch.send(embed=emb)
            
        current_warns = get_user_data(user_id, 'warns', 0) + 1
        set_user_data(user_id, 'warns', current_warns)
        return

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} באוויר ומוגן קומפלט ב-Railway!")
    for guild in bot.guilds:
        try: bot.invites[guild.id] = await guild.invites()
        except: pass

@bot.event
async def on_member_join(member: discord.Member):
    global raid_mode_active
    now = datetime.now()
    
    join_tracker.append(now)
    globals()['join_tracker'] = [t for t in join_tracker if now - t < timedelta(seconds=10)]
    
    if len(join_tracker) > 8 and not raid_mode_active:
        raid_mode_active = True
        log_ch = member.guild.get_channel(CHANNELS["OWNER_LOGS"])
        if not log_ch:
            try: log_ch = await bot.fetch_channel(CHANNELS["OWNER_LOGS"])
            except: pass
        if log_ch:
            await log_ch.send("🚨 **מערכת ANTI-RAID הופעלה!** זוהה עומס כניסות חריג. חדר האימות ננעל זמנית להגנה!")
        
        verify_ch = member.guild.get_channel(1501316672345211041)
        if verify_ch:
            overwrite = verify_ch.overwrites_for(member.guild.default_role)
            overwrite.send_messages = False
            overwrite.view_channel = False
            await verify_ch.set_permissions(member.guild.default_role, overwrite=overwrite)

    # --- 🚨 מערכת ANTI-ALT ---
    try:
        now_utc = datetime.now(member.created_at.tzinfo)
        account_age = now_utc - member.created_at
        
        if account_age < timedelta(days=10):
            alt_ch = member.guild.get_channel(CHANNELS["ANTI_ALT"])
            if not alt_ch: alt_ch = await bot.fetch_channel(CHANNELS["ANTI_ALT"])
                
            if alt_ch:
                emb_alt = discord.Embed(title="התרעת משתמש חשוד (Anti-Alt) 🚨", color=0xffa500, timestamp=datetime.now())
                emb_alt.add_field(name="המשתמש:", value=f"{member.mention}\n({member.name})", inline=False)
                emb_alt.add_field(name="וותק חשבון:", value=f"{account_age.days} ימים", inline=False)
                await alt_ch.send(embed=emb_alt)
    except Exception as e:
        print(f"❌ [Anti-Alt Error]: {e}")

    # --- 🕒 מערכת בדיקת כניסה חוזרת (Rejoin Log) ---
    left_at_str = get_user_data(member.id, 'left_at', 0)
    if left_at_str != 0:
        try:
            left_at = datetime.fromisoformat(left_at_str)
            duration = now - left_at
            time_parts = []
            if duration.days > 0: time_parts.append(f"{duration.days} ימים")
            hours = duration.seconds // 3600
            if hours > 0: time_parts.append(f"{hours} שעות")
            minutes = (duration.seconds % 3600) // 60
            if minutes > 0: time_parts.append(f"{minutes} דקות")
            seconds = duration.seconds % 60
            if seconds > 0 or not time_parts: time_parts.append(f"{seconds} שניות")
            time_str = ", ".join(time_parts)
            
            log_ch = member.guild.get_channel(1505637562633420870)
            if not log_ch:
                try: log_ch = await bot.fetch_channel(1505637562633420870)
                except: pass
            if log_ch:
                await log_ch.send(f"ℹ️ המשתמש {member.mention} היה כבר בשרת ויצא לפני {time_str}")
        except: pass

    # --- 🎁 הודעת וולקאם ---
    welcome_ch = member.guild.get_channel(CHANNELS["WELCOME"])
    if not welcome_ch:
        try: welcome_ch = await bot.fetch_channel(CHANNELS["WELCOME"])
        except: pass
    try:
        new_invites = await member.guild.invites()
        bot.invites[member.guild.id] = new_invites  
    except: pass

    if welcome_ch and not raid_mode_active:
        emb_welcome = discord.Embed(title="👋 ברוך הבא לשרת!", description=f"ברוך הבא לשרת! מקווה שתהנה", color=0x00ff00, timestamp=now)
        emb_welcome.set_thumbnail(url=member.display_avatar.url)
        await welcome_ch.send(embed=emb_welcome)

@bot.event
async def on_member_remove(member: discord.Member):
    set_user_data(member.id, 'left_at', datetime.now().isoformat())

@bot.event
async def on_member_update(before, after):
    if before.premium_since is None and after.premium_since is not None:
        channel = after.guild.get_channel(CHANNELS["BOOST"])
        if not channel:
            try: channel = await bot.fetch_channel(CHANNELS["BOOST"])
            except: pass
        if channel:
            embed = discord.Embed(title="🚀 בוסט חדש לשרת!", description=f"תודה! **{after.name}** על הבוסט!\n\n**חבר:**\n{after.mention}", color=discord.Color.from_rgb(230, 28, 186), timestamp=datetime.now())
            embed.set_footer(text="תודה על התמיכה!!")
            await channel.send(content=after.mention, embed=embed)

# ==========================================
# --- 6. פקודות סטאפ ידניות ---
# ==========================================
@bot.tree.command(name="setup_tickets", description="[מנהל/אונר] פאנל דיווחים והצעות.")
async def s_tickets(i: discord.Interaction):
    v = ui.View(timeout=None)
    b_rep = ui.Button(label="🚨 דווח על שחקן", style=discord.ButtonStyle.danger, custom_id="tk_rep")
    b_sug = ui.Button(label="💡 שלח הצעה לשיפור", style=discord.ButtonStyle.secondary, custom_id="tk_sug")
    b_rep.callback = lambda inter: inter.response.send_modal(ReportModal())
    b_sug.callback = lambda inter: inter.response.send_modal(SuggestionModal())
    v.add_item(b_rep).add_item(b_sug)
    await i.channel.send("📩 **מרכז פניות ודיווחים - Tickets & Suggestions**", view=v)
    await i.response.send_message("✅ פאנל מאוחד הוקם!", ephemeral=True)

@bot.tree.command(name="setup_feedback", description="[מנהל/אונר] פאנל פידבקים.")
async def s_feedback(i: discord.Interaction):
    v = ui.View(timeout=None)
    b = ui.Button(label="📩 שלח פידבק", style=discord.ButtonStyle.primary, custom_id="f_open")
    b.callback = lambda inter: inter.response.send_modal(FeedbackModal())
    v.add_item(b)
    await i.channel.send("📩 **פאנל פידבקים רשמי**\nלחצו על הכפתור למטה כדי להביע את דעתכם על השרת!", view=v)
    await i.response.send_message("✅ פאנל פידבקים הוקם!", ephemeral=True)

@bot.tree.command(name="setup_verify", description="[מנהל/אונר] פאנל מערכת האימות.")
async def s_verify(i: discord.Interaction):
    emb = discord.Embed(title="🛡️ מערכת אימות הגנה - Verify", description="לחצו על הכפתור הירוק למטה כדי לקבל גישה לשרת.", color=0x00ff00)
    await i.channel.send(embed=emb, view=VerifyView())
    await i.response.send_message("✅ פאנל אימות הוקם!", ephemeral=True)

@bot.tree.command(name="unraid", description="[אונר בלבד] מכבה את מצב ה-Raid.")
async def unraid(i: discord.Interaction):
    global raid_mode_active
    raid_mode_active = False
    verify_ch = i.guild.get_channel(1501316672345211041)
    if verify_ch:
        overwrite = verify_ch.overwrites_for(i.guild.default_role)
        overwrite.send_messages = None
        overwrite.view_channel = True
        await verify_ch.set_permissions(i.guild.default_role, overwrite=overwrite)
    await i.response.send_message("🔓 מצב ה-Raid בוטל בהצלחה. חדר האימות נפתח מחדש!", ephemeral=True)

# ==========================================
# --- 7. פקודות מודרציה וניהול ---
# ==========================================

@bot.tree.command(name="account_age", description="[כללי] בודק לפני כמה ימים נוצר החשבון של המשתמש.")
async def account_age_cmd(i: discord.Interaction, member: discord.Member = None):
    target_member = member or i.user
    now_utc = datetime.now(target_member.created_at.tzinfo)
    diff = now_utc - target_member.created_at
    await i.response.send_message(f"📅 החשבון של {target_member.mention} נוצר לפני **{diff.days:,}** ימים!")

@bot.tree.command(name="warn", description="[מנהל/אונר] נותן אזהרה למשתמש.")
async def warn(i: discord.Interaction, member: discord.Member, reason: str):
    current_warns = get_user_data(member.id, 'warns', 0) + 1
    set_user_data(member.id, 'warns', current_warns)
    log = i.guild.get_channel(CHANNELS["WARNS_LOG"])
    if log: await log.send(f"⚠️ **אזהרה** | {member.mention} הוזהר על ידי {i.user.mention}. סיבה: `{reason}` (`{current_warns}/3`)")
    if current_warns >= 3: 
        role = i.guild.get_role(ROLES["MUTE"])
        if role: await member.add_roles(role)
    await i.response.send_message(f"✅ האזהרה נרשמה ({current_warns}/3).", ephemeral=True)

@bot.tree.command(name="unwarn", description="[מנהל/אונר] מוריד אזהרה אחת למשתמש.")
async def unwarn(i: discord.Interaction, member: discord.Member):
    current_warns = get_user_data(member.id, 'warns', 0)
    if current_warns <= 0: return await i.response.send_message("❌ למשתמש אין אזהרות.", ephemeral=True)
    set_user_data(member.id, 'warns', current_warns - 1)
    await i.response.send_message(f"✅ הורדה אזהרה. מצבו הנוכחי: `{current_warns - 1}/3`", ephemeral=True)

@bot.tree.command(name="mute", description="[מנהל/אונר] מקצה רול השתקה (Mute) למשתמש.")
async def mute(i: discord.Interaction, member: discord.Member, reason: str):
    role = i.guild.get_role(ROLES["MUTE"])
    if role: await member.add_roles(role)
    await i.response.send_message(f"🚫 {member.mention} הושתק. סיבה: `{reason}`")

@bot.tree.command(name="unmute", description="[מנהל/אונר] מסיר רול השתקה.")
async def unmute(i: discord.Interaction, member: discord.Member):
    role = i.guild.get_role(ROLES["MUTE"])
    if role: await member.remove_roles(role)
    await i.response.send_message(f"🔊 {member.mention} שוחרר מההשתקה.")

@bot.tree.command(name="kick", description="[מנהל/אונר] מגרש משתמש מהשרת.")
async def kick(i: discord.Interaction, member: discord.Member, reason: str):
    await member.kick(reason=reason)
    await i.response.send_message(f"👞 המשתמש `{member.name}` הועף מהשרת. סיבה: `{reason}`")

@bot.tree.command(name="ban", description="[מנהל/אונר] חוסם משתמש מהשרת.")
async def ban(i: discord.Interaction, member: discord.Member, reason: str):
    await member.ban(reason=reason)
    await i.response.send_message(f"🚫 המשתמש `{member.name}` נחסם מהשרת. סיבה: `{reason}`")

@bot.tree.command(name="clear", description="[מנהל/אונר] מוחק הודעות מהערוץ הנוכחי.")
async def clear(i: discord.Interaction, amount: int):
    if amount <= 0: return await i.response.send_message("❌ הזן מספר תקין.", ephemeral=True)
    await i.channel.purge(limit=amount)
    await i.response.send_message(f"🧹 נמחקו בהצלחה `{amount}` הודעות!", ephemeral=True)

if __name__ == "__main__":
    if TOKEN: bot.run(TOKEN)
    else: print("❌ שגיאה: לא נמצא DISCORD_TOKEN.")
