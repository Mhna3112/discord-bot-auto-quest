#!/usr/bin/env python3
"""
CTDOTEAM - Discord Quest Auto-Completer Bot Wrapper (Slash Command Version)
"""

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import os
import json
from dotenv import load_dotenv

# Import our async module
import main

# Load config from .env
load_dotenv()

# Setup bot with default prefix (still needed for commands.Bot base, but we use slash commands)
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Database file for tokens
DB_FILE = "tokens.json"

# State dictionary for active tasks and completers (mapped by user_id)
active_tasks = {}
active_completers = {}


# ── Database Helpers ──────────────────────────────────────────────────────────
def load_tokens() -> dict:
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {DB_FILE}: {e}")
        return {}


def save_token(user_id: int, token: str):
    tokens = load_tokens()
    tokens[str(user_id)] = token
    try:
        with open(DB_FILE, "w") as f:
            json.dump(tokens, f, indent=4)
    except Exception as e:
        print(f"Error saving token to {DB_FILE}: {e}")


def delete_token(user_id: int):
    tokens = load_tokens()
    user_str = str(user_id)
    if user_str in tokens:
        del tokens[user_str]
        try:
            with open(DB_FILE, "w") as f:
                json.dump(tokens, f, indent=4)
        except Exception as e:
            print(f"Error deleting token from {DB_FILE}: {e}")


# ── Discord Logging Callback ──────────────────────────────────────────────────
async def discord_log_callback(msg: str, level: str):
    """Callback to send log messages from main.py to the designated Discord channel."""
    # Filter out noisy levels (debug, progress, info) to avoid flooding the channel
    if level in ("debug", "progress", "info"):
        return

    channel_id = os.getenv("LOG_CHANNEL_ID")
    if not channel_id or channel_id == "YOUR_LOG_CHANNEL_ID_HERE":
        return
        
    try:
        channel = bot.get_channel(int(channel_id))
        if channel:
            emoji = {
                "ok": "✅",
                "warn": "⚠️",
                "error": "❌",
            }.get(level, "🔹")
            
            # Format and send the message
            await channel.send(f"{emoji} **[{level.upper()}]** {msg}")
    except Exception as e:
        print(f"Error sending log to channel: {e}")


# ── Discord Summary Report Callback ───────────────────────────────────────────
async def discord_report_callback(user_id: str, username: str, avatar_url: str, results: list):
    """Callback to send a summary embed report to the designated Discord log channel when a cycle completes."""
    channel_id = os.getenv("LOG_CHANNEL_ID")
    if not channel_id or channel_id == "YOUR_LOG_CHANNEL_ID_HERE":
        return
        
    try:
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return
            
        success_count = sum(1 for r in results if r["success"])
        total_count = len(results)
        
        embed = discord.Embed(
            color=discord.Color.green() if success_count == total_count else discord.Color.orange()
        )
        embed.set_author(name=username, icon_url=avatar_url)
        embed.title = "🎁 Quest Auto-Complete hoàn tất!"
        
        # Build description list matching user's layout
        desc_lines = [f"📜 **Kết quả ({success_count}/{total_count} thành công)**"]
        for r in results:
            mark = "✅" if r["success"] else "❌"
            desc_lines.append(f"{mark} {r['name']}")
        embed.description = "\n".join(desc_lines)
        
        # Set footer icon to bot's avatar or default discord icon
        bot_avatar_url = bot.user.avatar.url if bot.user.avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
        embed.set_footer(
            text=f"User ID: {user_id} • Quest Auto-Completer",
            icon_url=bot_avatar_url
        )
        embed.timestamp = discord.utils.utcnow()
        
        await channel.send(embed=embed)
    except Exception as e:
        print(f"Error sending summary report: {e}")


# Register callbacks with main module
main.register_log_callback(discord_log_callback)
main.register_report_callback(discord_report_callback)


# ── Bot Events ─────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print("=" * 40)
    print(f"Discord Bot logged in as: {bot.user.name} (ID: {bot.user.id})")
    print("=" * 40)
    
    # Initialize tokens.json if not exists
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump({}, f)
            
    # Sync command tree with Discord globally
    print("Syncing slash commands...")
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} slash command(s) globally.")
    except Exception as e:
        print(f"Error syncing commands: {e}")
            
    # Set status
    await bot.change_presence(activity=discord.Game(name="/help | Quest Auto-Completer"))


# ── Modals ────────────────────────────────────────────────────────────────────
class LoginModal(discord.ui.Modal, title="Đăng nhập tài khoản làm Quest"):
    token_input = discord.ui.TextInput(
        label="Discord User Token",
        placeholder="Dán token cá nhân của bạn vào đây...",
        style=discord.TextStyle.long,
        required=True,
        min_length=30
    )

    async def on_submit(self, interaction: discord.Interaction):
        token = self.token_input.value.strip()
        await interaction.response.defer(ephemeral=True)
        
        try:
            build_number = await main.fetch_latest_build_number()
            api = main.DiscordAPI(token, build_number)
            
            # Verify token by querying user info
            r = await api.get("/users/@me")
            if r.status != 200:
                await interaction.followup.send("❌ Token không hợp lệ. Vui lòng kiểm tra lại tài khoản hoặc token của bạn!", ephemeral=True)
                await api.close()
                return
                
            user_data = await r.json()
            username = user_data.get("username", "Unknown")
            await api.close()
            
            # Save token
            save_token(interaction.user.id, token)
            await interaction.followup.send(
                content=f"✅ Đăng nhập thành công!\n👤 Tài khoản liên kết: **@{username}**\nBây giờ bạn có thể dùng lệnh `/start` hoặc `/status`.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Có lỗi xảy ra trong quá trình đăng nhập: `{e}`", ephemeral=True)


# ── Slash Commands ─────────────────────────────────────────────────────────────
@bot.tree.command(name="help", description="Hiển thị hướng dẫn sử dụng bot.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Quest Auto-Completer Bot Commands",
        description="Quản lý việc tự động hoàn thành Discord Quests bằng lệnh Slash.",
        color=discord.Color.cyan()
    )
    embed.add_field(name="`/gettoken`", value="Lấy mã JavaScript để tự tìm token tài khoản Discord của bạn.", inline=False)
    embed.add_field(name="`/login`", value="Đăng nhập tài khoản làm nhiệm vụ của bạn (Mở popup nhập token bảo mật).", inline=False)
    embed.add_field(name="`/logout`", value="Đăng xuất và xóa token của bạn khỏi hệ thống.", inline=False)
    embed.add_field(name="`/status`", value="Xem danh sách quest hiện tại và tiến độ hoàn thành.", inline=False)
    embed.add_field(name="`/start`", value="Bắt đầu chạy quét/hoàn thành quest tự động chạy ngầm.", inline=False)
    embed.add_field(name="`/stop`", value="Dừng chạy quét quest tự động chạy ngầm.", inline=False)
    embed.add_field(name="`/check`", value="Chạy quét quest một lần thủ công lập tức.", inline=False)
    embed.add_field(name="`/help`", value="Hiển thị menu hướng dẫn này.", inline=False)
    embed.set_footer(text="CTDOTEAM - Quest Auto-Completer Bot")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="gettoken", description="Lấy đoạn mã JavaScript để tự tìm token tài khoản Discord của bạn.")
async def gettoken(interaction: discord.Interaction):
    instructions = (
        "💡 **Hướng dẫn lấy Discord User Token của bạn:**\n\n"
        "1. Mở Discord trên trình duyệt web (Chrome, Edge, Firefox...) và đăng nhập.\n"
        "2. Nhấn phím **F12** (hoặc `Ctrl + Shift + I`) để mở Công cụ nhà phát triển (Developer Tools).\n"
        "3. Chọn tab **Console** ở phía trên thanh công cụ.\n"
        "4. Copy toàn bộ đoạn mã trong khung dưới đây, dán vào tab Console rồi nhấn **Enter**.\n"
        "5. Token của bạn sẽ hiển thị ở dòng kết quả. Dùng lệnh `/login` để liên kết nó với Bot.\n\n"
        "⚠️ **CẢNH BÁO BẢO MẬT**: *Tuyệt đối KHÔNG chia sẻ token này với bất kỳ ai khác! Người khác có token này sẽ có toàn quyền truy cập và kiểm soát tài khoản của bạn.*"
    )
    
    code_block = (
        "```javascript\n"
        "(() => {\n"
        "  let token;\n"
        "  window.webpackChunkdiscord_app.push([[Symbol()], {}, req => {\n"
        "    for (let m of Object.values(req.c)) {\n"
        "      try {\n"
        "        if (!m.exports || m.exports === window) continue;\n"
        "        if (m.exports?.getToken) {\n"
        "          token = m.exports.getToken();\n"
        "          break;\n"
        "        }\n"
        "        for (let key in m.exports) {\n"
        "          if (m.exports?.[key]?.getToken && m.exports[key][Symbol.toStringTag] !== 'IntlMessagesProxy') {\n"
        "            token = m.exports[key].getToken();\n"
        "            break;\n"
        "          }\n"
        "        }\n"
        "        if (token) break;\n"
        "      } catch {}\n"
        "    }\n"
        "  }]);\n"
        "  window.webpackChunkdiscord_app.pop();\n"
        "  return token;\n"
        "})();\n"
        "```"
    )
    await interaction.response.send_message(content=f"{instructions}\n{code_block}", ephemeral=True)


@bot.tree.command(name="login", description="Đăng nhập tài khoản cá nhân để tự động làm quest (Mở popup điền bảo mật).")
async def login(interaction: discord.Interaction):
    # Sends a popup modal to input the token securely (arguments are not shown in chat)
    await interaction.response.send_modal(LoginModal())


@bot.tree.command(name="logout", description="Đăng xuất và xóa token của bạn khỏi hệ thống.")
async def logout(interaction: discord.Interaction):
    global active_tasks, active_completers
    user_id = interaction.user.id
    tokens = load_tokens()
    
    if str(user_id) not in tokens:
        await interaction.response.send_message("ℹ️ Bạn chưa đăng nhập hệ thống.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # If task is running, stop it
    if user_id in active_tasks and not active_tasks[user_id].done():
        active_completers[user_id].running = False
        try:
            await asyncio.wait_for(active_tasks[user_id], timeout=3.0)
        except Exception:
            active_tasks[user_id].cancel()
        del active_tasks[user_id]
        del active_completers[user_id]

    delete_token(user_id)
    await interaction.followup.send("✅ Đã đăng xuất và xóa dữ liệu token của bạn khỏi hệ thống.", ephemeral=True)


@bot.tree.command(name="start", description="Bắt đầu chạy quét/hoàn thành quest tự động chạy ngầm.")
async def start(interaction: discord.Interaction):
    global active_tasks, active_completers
    user_id = interaction.user.id
    tokens = load_tokens()

    if str(user_id) not in tokens:
        await interaction.response.send_message("❌ Bạn chưa đăng nhập tài khoản. Vui lòng DM riêng cho bot lệnh `/login`.", ephemeral=True)
        return

    if user_id in active_tasks and not active_tasks[user_id].done():
        await interaction.response.send_message("⚠️ Quest Auto-Completer đã đang hoạt động cho tài khoản của bạn rồi!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    token = tokens[str(user_id)]

    try:
        build_number = await main.fetch_latest_build_number()
        api = main.DiscordAPI(token, build_number)
        
        # Verify token details
        r = await api.get("/users/@me")
        if r.status != 200:
            await interaction.followup.send("❌ Token của bạn đã hết hạn hoặc không hợp lệ. Vui lòng `/login` lại!", ephemeral=True)
            await api.close()
            return
            
        user_data = await r.json()
        username = user_data.get("username", "Unknown")
        user_id_str = user_data.get("id")
        avatar_hash = user_data.get("avatar")
        
        if avatar_hash:
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id_str}/{avatar_hash}.png"
        else:
            avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
        
        # Initialize and run completer loop
        completer = main.QuestAutocompleter(api, user_id_str, username, avatar_url)
        active_completers[user_id] = completer
        active_tasks[user_id] = asyncio.create_task(completer.run())
        
        await interaction.followup.send(f"🚀 Trình quét ngầm cho tài khoản **@{username}** đã được khởi chạy thành công!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Khởi động thất bại: `{e}`", ephemeral=True)


@bot.tree.command(name="stop", description="Dừng chạy quét quest tự động chạy ngầm.")
async def stop(interaction: discord.Interaction):
    global active_tasks, active_completers
    user_id = interaction.user.id
    
    if user_id not in active_tasks or active_tasks[user_id].done():
        await interaction.response.send_message("ℹ️ Trình quét quest hiện tại đang không chạy cho tài khoản của bạn.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    
    completer = active_completers[user_id]
    completer.running = False
    
    try:
        await asyncio.wait_for(active_tasks[user_id], timeout=5.0)
    except asyncio.TimeoutError:
        active_tasks[user_id].cancel()
        await interaction.followup.send("⚠️ Vòng lặp quét không dừng kịp và đã bị buộc dừng.", ephemeral=True)
    else:
        await interaction.followup.send("✅ Đã dừng hoạt động quét quest thành công.", ephemeral=True)
    finally:
        if completer.api:
            await completer.api.close()
        # Cleanup state
        if user_id in active_tasks:
            del active_tasks[user_id]
        if user_id in active_completers:
            del active_completers[user_id]


@bot.tree.command(name="status", description="Xem danh sách quest hiện tại và tiến độ hoàn thành.")
async def status(interaction: discord.Interaction):
    user_id = interaction.user.id
    tokens = load_tokens()

    if str(user_id) not in tokens:
        await interaction.response.send_message("❌ Bạn chưa đăng nhập tài khoản. Vui lòng DM riêng cho bot lệnh `/login`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    token = tokens[str(user_id)]

    try:
        build_number = await main.fetch_latest_build_number()
        api = main.DiscordAPI(token, build_number)
        
        r = await api.get("/quests/@me")
        if r.status != 200:
            await interaction.followup.send(f"❌ Lỗi lấy thông tin quest (HTTP Status: {r.status})", ephemeral=True)
            await api.close()
            return
            
        data = await r.json()
        await api.close()
        
        quests = data.get("quests", []) if isinstance(data, dict) else data
        if not quests:
            await interaction.followup.send("ℹ️ Không có quest khả dụng nào tại thời điểm này.", ephemeral=True)
            return

        embeds = []
        current_embed = discord.Embed(
            title="Trạng thái Discord Quests",
            description=f"Tìm thấy {len(quests)} nhiệm vụ.",
            color=discord.Color.purple()
        )
        
        for q in quests:
            # Discord embeds limit is 25 fields per embed
            if len(current_embed.fields) >= 25:
                embeds.append(current_embed)
                current_embed = discord.Embed(
                    title="Trạng thái Discord Quests (Tiếp theo)",
                    color=discord.Color.purple()
                )
                
            name = main.get_quest_name(q)
            task_type = main.get_task_type(q) or "Không hỗ trợ"
            
            if main.is_completed(q):
                status_str = "✅ Đã hoàn thành"
            elif main.is_enrolled(q):
                status_str = "▶️ Đang hoạt động (Enrolled)"
            elif main.is_completable(q):
                status_str = "⚪ Khả dụng (Chưa nhận)"
            else:
                status_str = "❌ Hết hạn / Không hỗ trợ"
                
            progress_val = main.get_seconds_done(q)
            target_val = main.get_seconds_needed(q)
            
            current_embed.add_field(
                name=name,
                value=f"• **Trạng thái**: {status_str}\n• **Nhiệm vụ**: `{task_type}`\n• **Tiến độ**: `{progress_val:.0f}/{target_val}s`",
                inline=False
            )
            
        embeds.append(current_embed)
        # Discord supports up to 10 embeds per message (maximum 250 fields)
        await interaction.followup.send(embeds=embeds[:10], ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Có lỗi xảy ra: `{e}`", ephemeral=True)


@bot.tree.command(name="check", description="Chạy quét quest một lần thủ công lập tức và hiển thị kết quả.")
async def check(interaction: discord.Interaction):
    global active_tasks, active_completers
    user_id = interaction.user.id
    tokens = load_tokens()

    if str(user_id) not in tokens:
        await interaction.response.send_message("❌ Bạn chưa đăng nhập tài khoản. Vui lòng DM riêng cho bot lệnh `/login`.", ephemeral=True)
        return

    if user_id in active_tasks and not active_tasks[user_id].done():
        await interaction.response.send_message("⚠️ Bạn đang chạy trình quét tự động dưới nền. Vui lòng sử dụng lệnh `/status` để theo dõi.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    token = tokens[str(user_id)]

    try:
        build_number = await main.fetch_latest_build_number()
        api = main.DiscordAPI(token, build_number)
        
        r = await api.get("/users/@me")
        if r.status != 200:
            await interaction.followup.send("❌ Token không hợp lệ.", ephemeral=True)
            await api.close()
            return
            
        user_data = await r.json()
        username = user_data.get("username", "Unknown")
        user_id_str = user_data.get("id")
        avatar_hash = user_data.get("avatar")
        
        if avatar_hash:
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id_str}/{avatar_hash}.png"
        else:
            avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
            
        completer = main.QuestAutocompleter(api, user_id_str, username, avatar_url)
        completer.running = True
        
        quests = await completer.fetch_quests()
        if not quests:
            await interaction.followup.send("ℹ️ Không tìm thấy nhiệm vụ nào.", ephemeral=True)
            await api.close()
            return
            
        quests = await completer.auto_accept(quests)
        
        actionable = [
            q for q in quests
            if main.is_enrolled(q) and not main.is_completed(q) and main.is_completable(q)
        ]
        
        if not actionable:
            # Build and send empty result summary report matching user's layout
            embed = discord.Embed(color=discord.Color.blue())
            embed.set_author(name=username, icon_url=avatar_url)
            embed.title = "🎁 Quest Auto-Complete hoàn tất!"
            embed.description = "📜 **Kết quả (0/0 thành công)**\nKhông có nhiệm vụ nào cần thực hiện lúc này."
            
            bot_avatar_url = bot.user.avatar.url if bot.user.avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
            embed.set_footer(text=f"User ID: {user_id_str} • Quest Auto-Completer", icon_url=bot_avatar_url)
            embed.timestamp = discord.utils.utcnow()
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            results = []
            for q in actionable:
                name = main.get_quest_name(q)
                try:
                    task_type = main.get_task_type(q)
                    if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
                        await completer.complete_video(q)
                    elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
                        await completer.complete_heartbeat(q)
                    elif task_type == "PLAY_ACTIVITY":
                        await completer.complete_activity(q)
                    results.append({"name": name, "success": True})
                except Exception as e:
                    results.append({"name": name, "success": False})
            
            success_count = sum(1 for r in results if r["success"])
            total_count = len(results)
            
            embed = discord.Embed(
                color=discord.Color.green() if success_count == total_count else discord.Color.orange()
            )
            embed.set_author(name=username, icon_url=avatar_url)
            embed.title = "🎁 Quest Auto-Complete hoàn tất!"
            
            desc_lines = [f"📜 **Kết quả ({success_count}/{total_count} thành công)**"]
            for r in results:
                mark = "✅" if r["success"] else "❌"
                desc_lines.append(f"{mark} {r['name']}")
            embed.description = "\n".join(desc_lines)
            
            bot_avatar_url = bot.user.avatar.url if bot.user.avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
            embed.set_footer(text=f"User ID: {user_id_str} • Quest Auto-Completer", icon_url=bot_avatar_url)
            embed.timestamp = discord.utils.utcnow()
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        await api.close()
    except Exception as e:
        await interaction.followup.send(f"❌ Gặp lỗi khi xử lý: `{e}`", ephemeral=True)


# ── Entrypoint ─────────────────────────────────────────────────────────────────
def start_bot():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token or bot_token.startswith("YOUR_") or bot_token == "":
        print("❌ Lỗi: BOT_TOKEN chưa được cấu hình hợp lệ trong file `.env`!")
        print("Vui lòng cập nhật BOT_TOKEN trước khi chạy bot.")
        return

    try:
        bot.run(bot_token)
    except Exception as e:
        print(f"❌ Lỗi khởi chạy Bot: {e}")


if __name__ == "__main__":
    start_bot()
