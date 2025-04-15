from flask import Flask, request, abort
from linebot import LineBotApi
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, MemberJoinedEvent, MemberLeftEvent, TextMessageContent
from linebot.v3.messaging import PushMessageRequest
import mysql.connector
import os

# MySQL 連線設定
db = mysql.connector.connect(
    host=os.environ.get("DB_HOST"),
    user=os.environ.get("DB_USER"),
    password=os.environ.get("DB_PASSWORD"),
    database=os.environ.get("DB_NAME")
)
cursor = db.cursor()

# LINE 設定
app = Flask(__name__)
configuration = Configuration(access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"))
line_handler = WebhookHandler(os.environ.get("LINE_CHANNEL_SECRET"))


def get_all_group_member_ids(group_id):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        all_ids = []
        start = None

        while True:
            response = line_bot_api.get_group_member_ids(group_id, start=start)
            all_ids.extend(response.member_ids)

            if not response.next:
                break
            start = response.next

        return all_ids


def get_unregistered_members(group_id):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        member_ids = get_all_group_member_ids(group_id)

        if not member_ids:
            return ["⚠️ 無法取得群組成員 ID，請確認 BOT 已加入群組"]

        # 查已註冊 userId
        registered_ids = set()
        batch_size = 100  # 避免 IN (...) 太長
        for i in range(0, len(member_ids), batch_size):
            batch = member_ids[i:i+batch_size]
            placeholders = ', '.join(['%s'] * len(batch))
            query = f"SELECT userId FROM player WHERE userId IN ({placeholders})"
            cursor.execute(query, tuple(batch))
            registered_ids.update(user_id for (user_id,) in cursor.fetchall())

        # 未註冊的 ID
        unregistered_ids = [uid for uid in member_ids if uid not in registered_ids]

        # 查名字
        unregistered_names = []
        for uid in unregistered_ids:
            try:
                profile = line_bot_api.get_group_member_profile(group_id, uid)
                unregistered_names.append(profile.display_name)
            except:
                unregistered_names.append(f"無法取得名稱 ({uid[:6]})")

        return unregistered_names




# ----------- LINE CALLBACK ------------
@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        line_handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'

# ----------- LINE 訊息處理 ------------
@line_handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try: 
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            message = event.message.text

            if message.startswith("bot/以Line名稱查詢/"):
                queryName = message.split("/")[2]
                cursor.execute("SELECT gameName FROM player WHERE userName = %s", (queryName,))
                results = cursor.fetchall()
                if results:
                    reply = f"Line名稱 {queryName} 查詢結果：\n" + "\n".join(f"遊戲名稱：{r[0]}" for r in results)
                else:
                    reply = f"找不到 Line名稱 {queryName} 的紀錄"

            elif message.startswith("bot/以遊戲名稱查詢/"):
                gameName = message.split("/")[2]
                cursor.execute("SELECT userName FROM player WHERE gameName = %s", (gameName,))
                result = cursor.fetchone()
                if result:
                    reply = f"遊戲名稱 {gameName} 查詢結果：\nLine名稱：{result[0]}"
                else:
                    reply = f"找不到遊戲名稱 {gameName} 的紀錄"

            elif message == "bot/功能查詢":
                reply = "\n".join([
                    "bot/以Line名稱查詢/oooo",
                    "bot/以遊戲名稱查詢/oooo",
                    "創建帳號：https://liff.line.me/2006989473-gqajDkdd"
                ])
                
            
            elif message == "bot/名單":
                cursor.execute("SELECT userName, gameName FROM player")
                results = cursor.fetchall()

                if results:
                    reply_lines = [f"{i+1}. {user}｜{game}" for i, (user, game) in enumerate(results)]
                    reply = "目前名單如下：\n" + "\n".join(reply_lines)
                else:
                    reply = "目前尚無資料。"

            elif message == "bot/查詢未登錄成員":
                if hasattr(event.source, "group_id"):
                    groupId = event.source.group_id
                    unregistered_names = get_unregistered_members(groupId)

                    if unregistered_names:
                        reply_lines = [f"{i+1}. {name}" for i, name in enumerate(unregistered_names)]
                        reply = "以下成員尚未登錄遊戲帳號：\n" + "\n".join(reply_lines)
                    else:
                        reply = "所有成員都已經完成登錄"
                else:
                    reply = "請在群組中使用此指令。"

            elif message.startswith("bot"):
                reply = "請輸入正確格式的指令喔！"

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
    except Exception as e:
        print("❌ 錯誤發生：", e)
        abort(500)

# ----------- 成員退出自動刪除資料 ------------
@line_handler.add(MemberLeftEvent)
def handle_leave(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        userId = event.left.members[0].user_id
        groupId = event.source.group_id

        cursor.execute("SELECT * FROM player WHERE userId = %s", (userId,))
        results = cursor.fetchall()

        if results:
            lineName = results[0][1]  # 取第一筆的 userName
        else:
            lineName = "未知使用者"

        cursor.execute("DELETE FROM player WHERE userId = %s", (userId,))
        db.commit()

        reply = [f"{lineName} 退出群組，已刪除遊戲帳號："]
        reply += [f"遊戲名稱：{r[2]}" for r in results]

        line_bot_api.push_message(
            PushMessageRequest(
                to=groupId,
                messages=[TextMessage(text="\n".join(reply))]
                ) 
            )

# ----------- 成員加入歡迎訊息 ------------
@line_handler.add(MemberJoinedEvent)
def handle_join(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        groupId = event.source.group_id
        reply_message = [
            "歡迎加入天謀雲雨群組",
            "以下為本群機器人功能：",
            "bot/以Line名稱查詢/oooo",
            "bot/以遊戲名稱查詢/oooo",
            "創建帳號：https://liff.line.me/2006989473-gqajDkdd"
        ]
        line_bot_api.push_message(
            PushMessageRequest(
                to=groupId,
                messages=[TextMessage(text="\n".join(reply_message))]
            )
        )

# ----------- 啟動 Flask 伺服器 ------------
if __name__ == "__main__":
    app.run()
