# bot.py
import os
import uuid
import json
import discord
from discord.ext import commands, tasks
from flask import Flask
from supabase import create_client, Client
from threading import Thread
import psutil
import GPUtil

# ---------------- Render keep_alive ----------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is alive!"

def keep_alive():
    Thread(target=lambda: app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        debug=False,
        use_reloader=False
    )).start()

# ---------------- Config ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
AUTHOR_ICON_URL = os.environ.get("AUTHOR_ICON_URL", "https://i.postimg.cc/kX151Rzr/18174-600x600.jpg")

ADMIN_NOTIFY_ROLE_ID = int(os.environ.get("ADMIN_NOTIFY_ROLE_ID", 1434213717406515392))
DELIVERY_LOG_ROLE_ID = int(os.environ.get("DELIVERY_LOG_ROLE_ID", 1434213717406515392))
PURCHASE_LOG_CHANNEL_ID = int(os.environ.get("PURCHASE_LOG_CHANNEL_ID", 1434209073359880263))

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY must be set in env")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

purchases = {}
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------- Admin DM 操作 ----------------
class AdminActionView(discord.ui.View):
    def __init__(self, pid):
        super().__init__(timeout=None)
        self.pid = pid

    @discord.ui.button(label="拒否", style=discord.ButtonStyle.danger)
    async def reject(self, interaction, _):
        p = purchases.get(self.pid)
        if not p:
            return await interaction.response.send_message("購入情報なし", ephemeral=True)
        try:
            user = await bot.fetch_user(p["buyer_id"])
            await user.send(f"購入は拒否されました。")
        except: pass
        await interaction.response.send_message("拒否通知完了", ephemeral=True)

    @discord.ui.button(label="配達", style=discord.ButtonStyle.success)
    async def deliver(self, interaction, _):
        p = purchases.get(self.pid)
        if not p:
            return await interaction.response.send_message("情報なし", ephemeral=True)
        try:
            buyer = await bot.fetch_user(p["buyer_id"])
            if p["file_path"]:
                await buyer.send(
                    f"ご購入ありがとうございます！\n商品: {p['product']}\n数量: 1",
                    file=discord.File(p["file_path"])
                )
            else:
                await buyer.send(f"ご購入ありがとうございます！\n商品: {p['product']}\n数量: 1")
        except: pass
        await interaction.response.send_message("配達完了しました。", ephemeral=True)

# ---------------- 商品セレクト ----------------
class ProductSelect(discord.ui.Select):
    def __init__(self, buyer, guild, file3, file22):
        options = [
            discord.SelectOption(label="小学生 (3個)", description="値段: 300円"),
            discord.SelectOption(label="詰め合わせパック(22個)", description="値段: 900円"),
        ]
        super().__init__(options=options, placeholder="商品を選択してください")
        self.buyer = buyer
        self.guild = guild
        self.file3 = file3
        self.file22 = file22

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected.startswith("小学生"):
            product = "小学生 (3個)"
            price = "300円"
            file_path = self.file3
        else:
            product = "詰め合わせパック(22個)"
            price = "900円"
            file_path = self.file22

        purchase_id = str(uuid.uuid4())
        purchases[purchase_id] = {
            "product": product,
            "price": price,
            "buyer_id": str(self.buyer.id),
            "buyer_name": str(self.buyer),
            "guild_id": str(self.guild.id),
            "guild_name": self.guild.name,
            "file_path": file_path,
        }

        # ローカル保存
        purchase_txt = os.path.join(DATA_DIR, f"{purchase_id}.txt")
        with open(purchase_txt, "w", encoding="utf-8") as f:
            json.dump(purchases[purchase_id], f, ensure_ascii=False, indent=2)

        # 管理者 DM に配達ボタン
        role = self.guild.get_role(ADMIN_NOTIFY_ROLE_ID)
        if role:
            for m in role.members:
                try:
                    embed = discord.Embed(title=f"{product} の購入希望", color=0xFFFFFF)
                    embed.add_field(name="購入者", value=f"{self.buyer} ({self.buyer.id})")
                    await m.send(embed=embed, view=AdminActionView(purchase_id))
                except: pass

        await interaction.response.send_message("管理者に通知しました。商品は管理者が配達ボタンを押すまで届きません。", ephemeral=True)

class ProductSelectView(discord.ui.View):
    def __init__(self, buyer, guild, file3, file22):
        super().__init__(timeout=None)
        self.add_item(ProductSelect(buyer, guild, file3, file22))

# ---------------- パネル ----------------
class PanelButtons(discord.ui.View):
    def __init__(self, file3=None, file22=None):
        super().__init__(timeout=None)
        self.file3 = file3
        self.file22 = file22

    @discord.ui.button(label="🛒｜購入する", style=discord.ButtonStyle.success)
    async def buy(self, interaction, _):
        await interaction.response.send_message(view=ProductSelectView(interaction.user, interaction.guild, self.file3, self.file22))

    @discord.ui.button(label="🔍｜在庫確認", style=discord.ButtonStyle.primary)
    async def stock(self, interaction, _):
        embed = discord.Embed(title="在庫確認", color=0xFFFFFF)
        embed.add_field(name="小学生 (3個)", value="価格: ¥300 | 在庫: ∞")
        embed.add_field(name="詰め合わせパック(22個)", value="価格: ¥900 | 在庫: ∞")
        await interaction.response.send_message(embed=embed)

# ---------------- /vd-panel-001 ----------------
@bot.tree.command(name="vd-panel-001")
async def vd_panel(interaction: discord.Interaction, file3: discord.Attachment, file22: discord.Attachment):
    path3 = os.path.join(DATA_DIR, file3.filename)
    path22 = os.path.join(DATA_DIR, file22.filename)
    await file3.save(path3)
    await file22.save(path22)

    embed = discord.Embed(title="🔞｜PAYPAY半自販機", description="下記ボタンを押して購入してください", color=0xFFFFFF)
    embed.set_author(name="半自販機パネル", icon_url=AUTHOR_ICON_URL)
    embed.set_footer(text="Cats Shop bot v3 からのDMを許可してください")
    embed.add_field(name="小学生 (3個)", value="値段: 300円")
    embed.add_field(name="詰め合わせパック(22個)", value="値段: 900円")

    await interaction.response.send_message(embed=embed, view=PanelButtons(path3, path22))

# ---------------- Bot Ready ----------------
@bot.event
async def on_ready():
    bot.add_view(PanelButtons())
    print(f"✅ Bot Ready: {bot.user} / ID: {bot.user.id}")
    try:
        await bot.tree.sync()
        print("✅ コマンド同期成功")
    except Exception as e:
        print(f"❌ コマンド同期失敗: {e}")
    update_status.start()

# ---------------- ステータス更新 ----------------
@tasks.loop(minutes=5)
async def update_status():
    try:
        ping = round(bot.latency * 1000)
        commands_count = len(bot.tree.get_commands())
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        gpus = GPUtil.getGPUs()
        if gpus:
            gpu_usage = gpus[0].load * 100
            gpu_mem = gpus[0].memoryUtil * 100
        else:
            gpu_usage = 0
            gpu_mem = 0
        status_text = f"{ping}ms ping | {commands_count} command | CPU {cpu}%/{mem}% | GPU {gpu_usage:.1f}%/{gpu_mem:.1f}%"
        await bot.change_presence(activity=discord.Game(status_text))
    except Exception as e:
        print("ステータス更新エラー:", e)

# ---------------- Main ----------------
keep_alive()
bot.run(BOT_TOKEN)
