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

            if message.startswith("bot/查詢/"):
                queryName = message.split("/")[2]
                cursor.execute("SELECT gameName, league, camp FROM player WHERE userName = %s", (queryName,))
                results_line = cursor.fetchall()

                db.commit()

                if results_line:
                    reply = f"Line名稱 {queryName} 查詢結果：\n" + "\n".join(f"遊戲名稱：{r[0]}\n所屬聯盟：{r[1]} 分營：{r[2]}" for r in results)
                else:
                    reply = f"找不到 Line名稱 {queryName} 的紀錄"

                reply.join("=================")

                cursor.execute("SELECT userName, league, camp FROM player WHERE gameName = %s", (queryName,))
                result = cursor.fetchone()
                
                db.commit()

                if result:
                    reply.join(f"遊戲名稱 {queryName} 查詢結果：\nLine名稱：{result[0]}\n所屬聯盟：{result[1]} 分營：{result[2]}")
                else:
                    reply = f"找不到遊戲名稱 {queryName} 的紀錄"
            # if message.startswith("bot/以Line名稱查詢/"):
            #     queryName = message.split("/")[2]
            #     cursor.execute("SELECT gameName, league, camp FROM player WHERE userName = %s", (queryName,))
            #     results = cursor.fetchall()

            #     db.commit()

            #     if results:
            #         reply = f"Line名稱 {queryName} 查詢結果：\n" + "\n".join(f"遊戲名稱：{r[0]}\n所屬聯盟：{r[1]} 分營：{r[2]}" for r in results)
            #     else:
            #         reply = f"找不到 Line名稱 {queryName} 的紀錄"

            # elif message.startswith("bot/以遊戲名稱查詢/"):
            #     gameName = message.split("/")[2]
            #     cursor.execute("SELECT userName, league, camp FROM player WHERE gameName = %s", (gameName,))
            #     result = cursor.fetchone()

            #     db.commit()

            #     if result:
            #         reply = f"遊戲名稱 {gameName} 查詢結果：\nLine名稱：{result[0]}\n所屬聯盟：{result[1]} 分營：{result[2]}"
            #     else:
            #         reply = f"找不到遊戲名稱 {gameName} 的紀錄"

            elif message == "bot/功能查詢":
                reply = "\n".join([
                    "bot/名單",
                    "bot/以Line名稱查詢/oooo",
                    "bot/以遊戲名稱查詢/oooo",
                    "點按連結將帳號加入資料庫：",
                    "https://liff.line.me/2007275305-5B4p9VMY",
                    "請注意，搜尋功能需使用全名"
                ])
                
            
            elif message == "bot/名單":
                cursor.execute("SELECT userName, gameName, league, camp FROM player")
                results = cursor.fetchall()
                
                db.commit()

                if results:
                    reply_lines = [f"{i+1}. {user}｜{game} \n  所屬聯盟：{league} 分營：{camp}" for i, (user, game, league, camp) in enumerate(results)]
                    reply = "目前名單如下：\n" + "\n".join(reply_lines)
                else:
                    reply = "目前尚無資料。"

            elif message.startswith("bot") or message.startswith("Bot"):
                reply = "未知指令格式，請使用\"bot/功能查詢\" 查詢所有機器人功能"

            elif message == "groupId":
                reply = event.source.group_id

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
    except Exception as e:
        import traceback
        print("❌ 錯誤發生：", e)
        traceback.print_exc()
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
            "歡迎加入天謀雲月群組🥳",
            "本群除政治外都可聊，訊息多可關提醒，遊戲內必要、緊急情況才會@all😁",
            "以下為本群機器人功能：",
            "bot/名單",
            "bot/功能查詢"
            "bot/以Line名稱查詢/oooo",
            "bot/以遊戲名稱查詢/oooo",
            "點按連結將帳號加入資料庫：",
            "https://liff.line.me/2007275305-5B4p9VMY"
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
