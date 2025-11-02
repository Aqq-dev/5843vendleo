# bot.py
import os
import uuid
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
from supabase import create_client, Client

# ---------------- Render keep_alive ----------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is alive!"
def keep_alive():
    from threading import Thread
    Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))).start()

# ---------------- Config ----------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
AUTHOR_ICON_URL = "https://i.postimg.cc/kX151Rzr/18174-600x600.jpg"

ADMIN_NOTIFY_ROLE_ID = 1434213717406515392
DELIVERY_LOG_ROLE_ID = 1434213717406515392
PURCHASE_LOG_CHANNEL_ID = 1434209073359880263

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
purchases = {}
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------- UI Classes ----------------
class PurchaseModal(discord.ui.Modal):
    def __init__(self, product, price, buyer, guild, file_path):
        super().__init__(title="PayPayリンク確認")
        self.product = product
        self.price = price
        self.buyer = buyer
        self.guild = guild
        self.file_path = file_path
        self.link = discord.ui.TextInput(label="PayPayリンク", placeholder="https://pay.paypay.ne.jp/...", required=True)
        self.add_item(self.link)

    async def on_submit(self, interaction: discord.Interaction):
        link_value = self.link.value.strip()
        if not link_value.startswith("https://pay.paypay.ne.jp/"):
            await interaction.response.send_message("無効なリンクです。", ephemeral=True)
            return

        purchase_id = str(uuid.uuid4())
        purchases[purchase_id] = {
            "product": self.product,
            "price": self.price,
            "buyer_id": self.buyer.id,
            "buyer_name": str(self.buyer),
            "guild_id": self.guild.id,
            "guild_name": self.guild.name,
            "file_path": self.file_path,
        }

        # Supabase Storage にアップロード
        file_name = os.path.basename(self.file_path)
        with open(self.file_path, "rb") as f:
            supabase.storage.from_("purchases").upload(f"{purchase_id}/{file_name}", f, {"cacheControl": "3600", "upsert": True})

        # Supabase DB に購入履歴保存
        supabase.table("purchase_logs").insert({
            "id": purchase_id,
            "product": self.product,
            "price": self.price,
            "buyer_id": self.buyer.id,
            "buyer_name": str(self.buyer),
            "guild_id": self.guild.id,
            "guild_name": self.guild.name,
            "file_name": file_name,
            "paypay_link": link_value
        }).execute()

        # Discord 管理者通知
        embed = discord.Embed(title=f"{self.product} の購入希望が届きました", color=0xFFFFFF)
        embed.add_field(name="金額", value=self.price)
        embed.add_field(name="購入者", value=f"<@{self.buyer.id}> ({self.buyer.id}) {self.buyer}")
        embed.add_field(name="PayPayリンク", value=link_value)
        embed.set_footer(text="支払いを確認したら配達ボタンを押してください")
        embed.set_author(name="半自販機パネル", icon_url=AUTHOR_ICON_URL)
        view = AdminActionView(purchase_id)
        role = self.guild.get_role(ADMIN_NOTIFY_ROLE_ID)
        sent = 0
        if role:
            for m in role.members:
                try: await m.send(embed=embed, view=view); sent += 1
                except: pass

        await interaction.response.send_message(f"管理者へ通知しました（{sent}人）", ephemeral=True)

# ---------------- RejectModal, AdminActionView, ProductSelect, ProductSelectView, PanelButtons ----------------
# 前回と同じ内容（zip対応・永続ビュー・ボタン色）

# ---------------- Slash Command ----------------
class VdPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="vd-panel-001")
    async def panel(self, interaction: discord.Interaction, file3: discord.Attachment, file22: discord.Attachment):
        path3 = os.path.join(DATA_DIR, file3.filename)
        path22 = os.path.join(DATA_DIR, file22.filename)
        await file3.save(path3)
        await file22.save(path22)

        embed = discord.Embed(title="PAYPAY半自販機", description="下記ボタンを押して購入してください", color=0xFFFFFF)
        embed.set_author(name="半自販機パネル", icon_url=AUTHOR_ICON_URL)
        embed.set_footer(text="<@1434213209795199006> からのDMを許可してください")
        embed.add_field(name="🔞 小学生 (3個)", value="```値段: 300円```")
        embed.add_field(name="🔞 詰め合わせパック(22個)", value="```値段: 900円```")
        await interaction.response.send_message(embed=embed, view=PanelButtons(path3, path22))

# ---------------- Bot Ready ----------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    bot.add_view(PanelButtons("dummy1.zip", "dummy2.zip"))
    print(f"✅ Bot Ready: {bot.user} / ID: {bot.user.id}")

# ---------------- Main ----------------
keep_alive()
bot.add_cog(VdPanel(bot))
bot.run(BOT_TOKEN)
