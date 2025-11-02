# bot.py
import os
import uuid
import zipfile
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

ADMIN_NOTIFY_ROLE_ID = int(os.environ.get("ADMIN_NOTIFY_ROLE_ID", 1434451874882256969))
DELIVERY_LOG_ROLE_ID = int(os.environ.get("DELIVERY_LOG_ROLE_ID", 1434451874882256969))
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
ZIP_DIR = "zips"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(ZIP_DIR, exist_ok=True)

# ---------------- UI Classes ----------------
class PurchaseModal(discord.ui.Modal):
    def __init__(self, product, price, buyer, guild, file_paths):
        super().__init__(title="PayPayリンク確認")
        self.product = product
        self.price = price
        self.buyer = buyer
        self.guild = guild
        self.file_paths = file_paths
        self.link = discord.ui.TextInput(label="PayPayリンク", placeholder="https://pay.paypay.ne.jp/...", required=True)
        self.add_item(self.link)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        link_value = self.link.value.strip()
        if not link_value.startswith("https://pay.paypay.ne.jp/"):
            await interaction.followup.send("無効なリンクです。", ephemeral=True)
            return

        purchase_id = str(uuid.uuid4())
        zip_name = f"{purchase_id}.zip"
        zip_path = os.path.join(ZIP_DIR, zip_name)
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for fpath in self.file_paths:
                zipf.write(fpath, os.path.basename(fpath))

        purchases[purchase_id] = {
            "product": self.product,
            "price": self.price,
            "buyer_id": str(self.buyer.id),
            "buyer_name": str(self.buyer),
            "guild_id": str(self.guild.id),
            "guild_name": self.guild.name,
            "zip_path": zip_path,
            "status": "pending",
        }

        # Supabase DB に購入履歴保存
        supabase.table("purchase_logs").insert({
            "id": purchase_id,
            "product": self.product,
            "price": self.price,
            "buyer_id": str(self.buyer.id),
            "buyer_name": str(self.buyer),
            "guild_id": str(self.guild.id),
            "guild_name": self.guild.name,
            "file_name": zip_name,
            "paypay_link": link_value,
            "status": "pending"
        }).execute()

        # 管理者通知
        embed = discord.Embed(title=f"{self.product} の購入希望が届きました", color=0xFFFFFF)
        embed.add_field(name="金額", value=self.price, inline=False)
        embed.add_field(name="購入者", value=f"<@{self.buyer.id}> ({self.buyer.id}) {self.buyer}", inline=False)
        embed.add_field(name="PayPayリンク", value=link_value, inline=False)
        embed.set_footer(text="支払い確認後、配達ボタンを押してください")
        embed.set_author(name="半自販機パネル", icon_url=AUTHOR_ICON_URL)

        view = AdminActionView(purchase_id)
        sent = 0
        role = self.guild.get_role(ADMIN_NOTIFY_ROLE_ID)
        if role:
            for m in role.members:
                try:
                    await m.send(embed=embed, view=view)
                    sent += 1
                except: 
                    continue

        await interaction.followup.send(f"購入希望を管理者に送信しました（{sent}人）", ephemeral=True)

class RejectModal(discord.ui.Modal):
    def __init__(self, pid):
        super().__init__(title="拒否理由")
        self.pid = pid
        self.reason = discord.ui.TextInput(label="理由", style=discord.TextStyle.long)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        p = purchases.get(self.pid)
        if not p:
            return await interaction.response.send_message("購入情報なし", ephemeral=True)
        try:
            user = await bot.fetch_user(p["buyer_id"])
            await user.send(f"購入は拒否されました。\n理由:\n{self.reason.value}")
        except: pass
        supabase.table("purchase_logs").update({"status": "rejected", "rejected_reason": self.reason.value}).eq("id", self.pid).execute()
        await interaction.response.send_message("拒否し通知しました。", ephemeral=True)

class AdminActionView(discord.ui.View):
    def __init__(self, pid):
        super().__init__(timeout=None)
        self.pid = pid

    @discord.ui.button(label="拒否", style=discord.ButtonStyle.danger, custom_id="reject_button")
    async def reject(self, interaction, _):
        await interaction.response.send_modal(RejectModal(self.pid))

    @discord.ui.button(label="配達", style=discord.ButtonStyle.success, custom_id="deliver_button")
    async def deliver(self, interaction, _):
        p = purchases.get(self.pid)
        if not p or p.get("status") != "pending":
            return await interaction.response.send_message("情報なし、または既に処理済みです。", ephemeral=True)

        p["status"] = "delivered"
        try:
            buyer = await bot.fetch_user(p["buyer_id"])
            await buyer.send(
                f"ご購入ありがとうございます！\n商品: {p['product']}\n商品説明: __転売は禁止です！__\n数量: 1\n以下をお受け取りください:",
                file=discord.File(p["zip_path"])
            )
        except: pass

        guild = bot.get_guild(int(p["guild_id"]))
        role = guild.get_role(DELIVERY_LOG_ROLE_ID) if guild else None
        log = f"配達完了\n商品: {p['product']}\n購入者: {p['buyer_name']} ({p['buyer_id']})\nサーバー: {p['guild_name']} ({p['guild_id']})"
        if role:
            for m in role.members:
                try: await m.send(log)
                except: pass

        channel = bot.get_channel(PURCHASE_LOG_CHANNEL_ID)
        if channel:
            embed = discord.Embed(title="💹｜販売ログ", color=0xFFFFFF)
            embed.add_field(name="商品名", value=p['product'])
            embed.add_field(name="購入数", value="1個")
            embed.add_field(name="購入者", value=f"<@{p['buyer_id']}> ({p['buyer_id']})")
            embed.add_field(name="購入サーバー", value=f"{p['guild_name']} ({p['guild_id']})")
            await channel.send(embed=embed)

        supabase.table("purchase_logs").update({"status": "delivered"}).eq("id", self.pid).execute()
        await interaction.response.send_message("配達完了しました。", ephemeral=True)

# ---------------- ProductSelect & View ----------------
class ProductSelect(discord.ui.Select):
    def __init__(self, buyer, guild, file3, file22):
        options = [
            discord.SelectOption(label="小学生 (3個)", description="値段: 300円"),
            discord.SelectOption(label="詰め合わせパック(22個)", description="値段: 900円"),
        ]
        super().__init__(options=options, placeholder="商品を選択してください", custom_id="product_select")
        self.buyer = buyer
        self.guild = guild
        self.file3 = file3
        self.file22 = file22

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            view=PurchaseModal(
                "小学生 (3個)" if self.values[0].startswith("小学生") else "詰め合わせパック(22個)",
                "300円" if self.values[0].startswith("小学生") else "900円",
                self.buyer, self.guild,
                [self.file3] if self.values[0].startswith("小学生") else [self.file22]
            ),
            ephemeral=True
        )

class ProductSelectView(discord.ui.View):
    def __init__(self, user, guild, file3, file22):
        super().__init__(timeout=None)
        self.add_item(ProductSelect(user, guild, file3, file22))

# ---------------- PanelButtons ----------------
class PanelButtons(discord.ui.View):
    def __init__(self, file3=None, file22=None):
        super().__init__(timeout=None)
        self.file3 = file3
        self.file22 = file22

    @discord.ui.button(label="🛒｜購入する", style=discord.ButtonStyle.success, custom_id="buy_button")
    async def buy(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            view=ProductSelectView(interaction.user, interaction.guild, self.file3, self.file22),
            ephemeral=True
        )

    @discord.ui.button(label="🔍｜在庫確認", style=discord.ButtonStyle.primary, custom_id="stock_button")
    async def stock(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            embed=discord.Embed(
                title="在庫確認",
                description="小学生 (3個) : ¥300 | 在庫 ∞\n詰め合わせパック(22個) : ¥900 | 在庫 ∞",
                color=0xFFFFFF
            ),
            ephemeral=True
        )

# ---------------- bot.tree.command ----------------
@bot.tree.command(name="vd-panel-001")
async def vd_panel(interaction: discord.Interaction, file3: discord.Attachment, file22: discord.Attachment):
    await interaction.response.defer(ephemeral=False)
    path3 = os.path.join(DATA_DIR, file3.filename)
    path22 = os.path.join(DATA_DIR, file22.filename)
    await file3.save(path3)
    await file22.save(path22)

    embed = discord.Embed(title="🔞｜PAYPAY半自販機", description="下記ボタンを押して購入してください", color=0xFFFFFF)
    embed.set_author(name="半自販機パネル", icon_url=AUTHOR_ICON_URL)
    embed.set_footer(text="Cats Shop bot v3 からのDMを許可してください")
    embed.add_field(name="小学生 (3個)", value="値段: 300円")
    embed.add_field(name="詰め合わせパック(22個)", value="値段: 900円")

    await interaction.followup.send(embed=embed, view=PanelButtons(path3, path22), ephemeral=False)

# ---------------- Bot Ready ----------------
@bot.event
async def on_ready():
    bot.add_view(PanelButtons())  # 永続ビュー
    print(f"✅ Bot Ready: {bot.user} / ID: {bot.user.id}")
    try:
        await bot.tree.sync()
        print("✅ グローバルコマンド同期成功")
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
        status_text = f"{ping}ms ping | synced {commands_count} command | CPU {cpu}%/{mem}% | GPU {gpu_usage:.1f}%/{gpu_mem:.1f}%"
        await bot.change_presence(activity=discord.Game(status_text))
    except Exception as e:
        print("ステータス更新エラー:", e)

# ---------------- Main ----------------
keep_alive()
bot.run(BOT_TOKEN)
