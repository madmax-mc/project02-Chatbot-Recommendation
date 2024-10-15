from flask import Flask, request, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, QuickReply, QuickReplyButton, MessageAction, FlexSendMessage
from neo4j import GraphDatabase
from pyngrok import ngrok
import datetime
import requests
import logging
from linebot.exceptions import LineBotApiError
from fuzzywuzzy import fuzz
from linebot.models import Profile
from linebot.models import FlexSendMessage, TextSendMessage

# Neo4j Aura credentials
URI = "neo4j://localhost"
AUTH = ("neo4j", "password")

# LINE Bot credentials
access_token = '------'
secret = '------'

line_bot_api = LineBotApi(access_token)
handler = WebhookHandler(secret)

# Neo4j connection
def run_query(query, parameters=None):
    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        with driver.session() as session:
            result = session.run(query, parameters)
            return [record for record in result]

# Store user states for conversation management
user_states = {}

# Config for llama API
llama_model = "supachai/llama-3-typhoon-v1.5"
url = "http://localhost:11434/api/generate"
headers = {
    "Content-Type": "application/json"
}

def is_relevant_question(msg):
    relevant_keywords = [
        "เครื่องสำอาง", "ความงาม", "น้ำหอม", "ผลิตภัณฑ์", "ดูแลผิว", "บำรุงผิว", "ลิปสติก", "แป้ง", "รองพื้น",
        "มาสคาร่า", "ครีม", "อายแชโดว์", "บลัชออน", "ไฮไลท์", "บรอนเซอร์", "คอนซีลเลอร์", "โทนเนอร์", "มอยส์เจอไรเซอร์",
        "เซรั่ม", "สครับ", "โฟมล้างหน้า", "คลีนซิ่ง", "มาส์กหน้า", "อายไลเนอร์", "ดินสอเขียนคิ้ว", "ลิปบาล์ม",
        "ลิปกลอส", "ลิปแมตต์", "ลิปทินท์", "บำรุงริมฝีปาก", "น้ำมันบำรุงผิว", "โกลว์", "กันแดด", "ปัดแก้ม",
        "พาเลตต์", "ขนตาปลอม", "อายครีม", "บาล์ม", "เนื้อแมตต์", "แชมพู", "ครีมนวดผม", "ผลิตภัณฑ์สำหรับผม",
        "น้ำมันหอมระเหย", "ผิวแพ้ง่าย", "ต่อต้านริ้วรอย", "ไวท์เทนนิ่ง", "ดีท็อกซ์", "เสริมสร้างผิว", "กลิ่นหอม",
        "น้ำหอมผู้หญิง", "น้ำหอมผู้ชาย", "โคโลญจน์", "เอสเซ้นส์", "โฟมล้างหน้า", "เจลบำรุงผิว", "โลชั่นบำรุงผิว",
        "ผิวมัน", "ผิวแห้ง", "ผิวผสม", "ลดจุดด่างดำ", "ลดริ้วรอย", "สิว", "ลดสิว", "ขัดผิว", "ผิวกระจ่างใส"
    ]
    
    # ใช้ fuzzy matching เพื่อหาคำที่ใกล้เคียง
    for keyword in relevant_keywords:
        if fuzz.partial_ratio(msg.lower(), keyword) > 80:  # ถ้าคล้ายกันเกิน 80% ถือว่าเป็นคำถามเกี่ยวข้อง
            return True
    
    return False

# llama response function with male customer service persona
def llama_response(msg, history_chat):
    print("======this is llama=====")
    
    # ตรวจสอบว่าคำถามเกี่ยวข้องหรือไม่
    if not is_relevant_question(msg):
        return "ขออภัยครับ ฉันสามารถตอบคำถามเกี่ยวกับเครื่องสำอาง ผลิตภัณฑ์ความงาม หรือน้ำหอมเท่านั้นครับ"
    
    # Combine user prompt with chat history
    history = "\n".join(history_chat)  # Join the history into a single string
    full_prompt = f"{history}\nUser: {msg}\nBot (male customer service agent): "  # Format the prompt with history

    payload = {
        "model": llama_model,
        "prompt": full_prompt + "ตอบคำถามเกี่ยวกับเครื่องสำอาง ผลิตภัณฑ์ความงาม และน้ำหอม ให้สุภาพ ใช้ภาษาไทยสั้นกระชับ และลงท้ายด้วยคำว่า 'ครับ'",
        "stream": False,
        "options": {
            "num_predict": 100,  # Adjust as needed
            "num_ctx": 1024,
            "temperature": 0.8,
        }
    }

    try:
        # ส่งคำขอไปยัง Ollama API
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()  # ตรวจสอบสถานะการตอบกลับ

        # แปลงผลลัพธ์เป็น JSON และดึงข้อความคำตอบ
        res_JSON = response.json()
        res_text = res_JSON.get("response", "No response from the model.")
        return f"(BA Ollama): {res_text}"
    except requests.RequestException as e:
        return f"Error: {e}"

def send_reply(reply_token, messages):
    if not isinstance(messages, list):
        messages = [messages]
    try:
        line_bot_api.reply_message(reply_token, messages)
    except LineBotApiError as e:
        # Check for specific status codes and errors
        if e.status_code == 400:
            logging.error(f"Failed to send message due to invalid action: {e.error.message}")
        else:
            logging.error(f"LineBotApiError: {e}")
    except Exception as e:
        # Log general errors that are not specifically caught by LineBotApiError
        logging.error(f"Failed to send message: {e}")

# Flask app
app = Flask(__name__)

# Start ngrok tunnel
port = "5000"
ngrok.set_auth_token("------")
public_url = ngrok.connect(port).public_url
print(f"ngrok tunnel {public_url} -> http://127.0.0.1:{port}")

@app.route("/webhook", methods=['POST'])
def webhook():
    body = request.get_data(as_text=True)
    signature = request.headers['X-Line-Signature']
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400

    return 'OK'

def escape_quotes(s):
    return s.replace("'", "\\'").replace('"', '\\"')

def log_chat_history(user_id, user_name, user_message, bot_response):
    try:
        # Escape single quotes in the user_message and bot_response
        user_message = user_message.replace("'", "\\'")
        bot_response = bot_response.replace("'", "\\'")

        # Get the current date and time as a formatted string
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Create a node for the user if it doesn't exist
        user_query = f"""
        MERGE (u:User {{ user_id: '{user_id}' }})
        ON CREATE SET u.user_name = '{user_name}'
        """
        run_query(user_query)
        
        # Create a node for the chat message
        message_query = f"""
        CREATE (m:ChatMessage {{
            user_id: '{user_id}',
            user_name: '{user_name}',
            question: '{user_message}', 
            response: '{bot_response}', 
            timestamp: '{timestamp}'
        }})
        """
        run_query(message_query)
        
        # Create a relationship between the user and the message
        relationship_query = f"""
        MATCH (u:User {{ user_id: '{user_id}' }}), (m:ChatMessage {{ timestamp: '{timestamp}' }})
        CREATE (u)-[:SENT]->(m)
        """
        run_query(relationship_query)
        
    except LineBotApiError as e:
        logging.error(f"Failed to get profile: {e}")


def shorten_label(label, max_length=20):
    return label if len(label) <= max_length else label[:max_length-3] + '...'

def create_product_flex(brand, products):
    # สร้างบล็อกข้อมูลสำหรับแต่ละสินค้า
    product_contents = []
    for product in products:
        product_contents.append({
            "type": "bubble",
            "hero": {
                "type": "image",
                "url": product.get('image_url', 'default_image_url'),  # ใส่ URL ของรูปภาพสินค้า
                "size": "full",  # ขนาดเต็มของ Flex Bubble
                "aspectRatio": "3:2",  # อัตราส่วนของภาพ (เช่น 4:3)
                "aspectMode": "fit"  # ใช้ "fit" เพื่อให้ภาพไม่ถูกครอบตัด
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": product.get('title', 'ไม่มีชื่อสินค้า'),  # ตรวจสอบว่ามีชื่อสินค้าหรือไม่
                        "weight": "bold",
                        "size": "xl",
                        "wrap": True
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "lg",
                        "spacing": "sm",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"ราคา: {product.get('old_price', 'ไม่มีราคาเก่า')} บาท",
                                "wrap": True,
                                "size": "sm",
                                "decoration": "line-through"
                            },
                            {
                                "type": "text",
                                "text": f"ราคา: {product.get('new_price', 'ไม่มีราคาใหม่')} บาท",
                                "wrap": True,
                                "size": "md"
                            },
                            {
                                "type": "text",
                                "text": f"ส่วนลด: {product.get('discount', 'ไม่มีส่วนลด')}",
                                "wrap": True,
                                "size": "sm"
                            },
                            {
                                "type": "text",
                                "text": f"คะแนน: {product.get('rating', 'ไม่มีคะแนน')}",
                                "wrap": True,
                                "size": "sm"
                            }
                        ]
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "action": {
                            "type": "uri",
                            "label": "ดูรายละเอียด",
                            "uri": product.get('full_link', 'https://www.sephora.co.th')  # ลิงก์ไปยังสินค้าบนเว็บไซต์
                        }
                    }
                ]
            }
        })

    # กลับรายการ Flex Message ที่สร้างขึ้น
    return {
        "type": "carousel",
        "contents": product_contents
    }

# ฟังก์ชันสร้าง Flex Message สำหรับรายละเอียดสินค้า
def create_product_detail_flex(product_info):
    contents = [
        {
            "type": "text",
            "text": product_info['title'],  # ชื่อสินค้า
            "weight": "bold",
            "size": "xl",
            "wrap": True
        },
        {
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"แบรนด์: {product_info.get('brand', 'ไม่ระบุ')}",  # แบรนด์
                    "size": "sm",
                    "color": "#666666",
                    "flex": 5
                }
            ]
        },
        {
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"ราคา: {product_info.get('old_price', 'ไม่ระบุ')} บาท",  # ราคาเก่า
                    "size": "sm",
                    "color": "#aaaaaa",
                    "flex": 5,
                    "decoration": "line-through"
                }
            ]
        },
        {
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"ราคา: {product_info.get('new_price', 'ไม่ระบุ')} บาท",  # ราคาใหม่
                    "size": "sm",
                    "color": "#666666",
                    "flex": 5
                }
            ]
        },
        {
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"คะแนน: {product_info.get('rating', 'ไม่ระบุ')}",  # คะแนน
                    "size": "sm",
                    "color": "#666666",
                    "flex": 5
                }
            ]
        }
    ]

    # เพิ่มข้อมูล "ประโยชน์"
    if product_info.get('benefits'):
        contents.append({
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"ประโยชน์: {product_info.get('benefits')}",  # ประโยชน์
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True
                }
            ]
        })

    # เพิ่ม "รายการประโยชน์"
    if product_info.get('benefits_list'):
        contents.append({
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"รายการประโยชน์: {product_info.get('benefits_list')}",  # รายการประโยชน์
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True
                }
            ]
        })

    # เพิ่ม "Product Claims"
    if product_info.get('product_claims'):
        contents.append({
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"Product Claims: {product_info.get('product_claims')}",  # Product Claims
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True
                }
            ]
        })

    # เพิ่ม "ส่วนประกอบ"
    if product_info.get('ingredients'):
        contents.append({
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"ส่วนประกอบ: {product_info.get('ingredients')}",  # ส่วนประกอบ
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True
                }
            ]
        })

    # เพิ่ม "วิธีใช้"
    if product_info.get('how_to_use'):
        contents.append({
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"วิธีใช้: {product_info.get('how_to_use')}",  # วิธีใช้
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True
                }
            ]
        })

    # เพิ่ม "ในเซ็ตประกอบด้วย"
    if product_info.get('set_contents'):
        contents.append({
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"ในเซ็ตประกอบด้วย: {'; '.join(product_info.get('set_contents', []))}",  # เซ็ตประกอบด้วย
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True
                }
            ]
        })

    # เพิ่ม "ข้อมูลน่ารู้"
    if product_info.get('facts'):
        contents.append({
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"ข้อมูลน่ารู้: {product_info.get('facts')}",  # ข้อมูลน่ารู้
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True
                }
            ]
        })

    return {
        "type": "bubble",
        "hero": {
            "type": "image",
            "url": product_info.get('image_url', 'default_image_url'),  # รูปภาพสินค้า
            "size": "full",
            "aspectRatio": "3:2",
            "aspectMode": "fit"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents  # เพิ่มข้อมูลต่าง ๆ ที่รวบรวมไว้
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "uri",
                        "label": "ดูรายละเอียดเพิ่มเติม",
                        "uri": product_info.get('full_link', 'https://www.sephora.co.th')  # ลิงก์ไปยังหน้าเว็บของสินค้า
                    }
                }
            ]
        }
    }

# ฟังก์ชันส่ง Flex Message พร้อมรายการสินค้า
# ฟังก์ชันส่ง Flex Message พร้อมรายการสินค้า
def send_flex_product_list(event, products, selected_brand):
    # ตรวจสอบว่า products เป็นลิสต์หรือไม่
    if not isinstance(products, list):
        print(f"Error: 'products' is expected to be a list, but got {type(products)}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เกิดข้อผิดพลาดในการดึงข้อมูลสินค้า"))
        return
    
    # ตรวจสอบว่าสินค้ามีอยู่จริงหรือไม่
    if len(products) == 0:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ไม่พบสินค้าจากแบรนด์ {selected_brand}"))
        return

    # จำนวนสินค้าที่มากสุดที่ Flex Message สามารถแสดงได้คือ 12 ชิ้น
    max_items_per_message = 12

    # แบ่งสินค้าที่มีเกิน 12 ชิ้นเป็นกลุ่มๆ (chunking)
    chunks = [products[i:i + max_items_per_message] for i in range(0, len(products), max_items_per_message)]

    try:
        # ส่ง Flex Message สำหรับกลุ่มแรกด้วย reply_message
        first_chunk = chunks[0]
        flex_message = FlexSendMessage(
            alt_text=f"สินค้าจากแบรนด์ {selected_brand}",
            contents=create_product_flex(selected_brand, first_chunk)  # ใช้เฉพาะกลุ่มแรก
        )
        
        # ส่ง Flex Message สำหรับกลุ่มแรก
        line_bot_api.reply_message(event.reply_token, flex_message)

    except LineBotApiError as e:
        print(f"Error sending reply message: {e}")
        line_bot_api.push_message(event.source.user_id, TextSendMessage(text="เกิดข้อผิดพลาดในการส่งข้อความแรก"))

    # ส่งสินค้าที่เหลือ (ถ้ามี) ด้วย push_message
    if len(chunks) > 1:
        for chunk in chunks[1:]:
            flex_message = FlexSendMessage(
                alt_text=f"สินค้าจากแบรนด์ {selected_brand}",
                contents=create_product_flex(selected_brand, chunk)  # ส่งกลุ่มที่เหลือ
            )
            line_bot_api.push_message(event.source.user_id, flex_message)

    # ไม่ต้องส่ง Quick Reply ที่นี่

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text

    # Log history before processing the message
    bot_response = ""

    # Fetch the user profile to log the name
    profile = line_bot_api.get_profile(user_id)
    user_name = profile.display_name  # เก็บชื่อผู้ใช้

    # ตรวจสอบหากผู้ใช้พิมพ์ "สวัสดี"
    if user_input.lower() == "สวัสดี":
        bot_response = ("สวัสดีครับ! ผมเป็นบอทบริการแนะนำสินค้าของ Sephora 🛍️"
                        "\nคุณสามารถถามเกี่ยวกับ เครื่องสำอาง, ผลิตภัณฑ์ความงาม, และ น้ำหอม ได้ครับ ✨"
                        "\n\n💡 ฟีเจอร์ที่คุณสามารถใช้งานได้:\n"
                        "1️⃣ ค้นหาสินค้าโปรโมชั่นล่าสุด 🔥\n"
                        "2️⃣ เลือกสินค้าแยกตามแบรนด์ 🏷️\n"
                        "3️⃣ ดูสินค้าที่มีส่วนลด 🎫\n"
                        "4️⃣ จัดเรียงสินค้าตามคะแนนรีวิว ⭐\n"
                        "5️⃣ ดูรายละเอียดของสินค้า 🛒\n"
                        "6️⃣ ถาม Ollama เพื่อแนะนำหรือสอบถามเกี่ยวกับเครื่องสำอาง ผลิตภัณฑ์ความงาม และน้ำหอม 💄"
                        "\n\nคุณสามารถพิมพ์ 'โปรโมชั่น' หรือใช้ ริชเมนู เพื่อเริ่มต้นใช้งานได้เลยครับ! 😊")
        send_reply(event.reply_token, TextSendMessage(text=bot_response))
        return

    # ตรวจสอบสถานะปัจจุบันของผู้ใช้
    user_state = user_states.get(user_id, {})

    if user_input == "ถาม ollama":
        # Prompt user for their question
        bot_response = "คุณมีคำถามอะไรที่ต้องการถาม Ollama?"
        user_states[user_id] = {"state": "WAITING_FOR_OLLAMA_QUESTION", "chat_history": []}
        send_reply(event.reply_token, TextSendMessage(text=bot_response))

    elif user_state.get("state") == "WAITING_FOR_OLLAMA_QUESTION":
        if user_input == "ถาม ollama อีกครั้ง":
            # Prompt user again without clearing history
            bot_response = "คุณมีคำถามอะไรอีกที่ต้องการถาม Ollama?"
            send_reply(event.reply_token, TextSendMessage(text=bot_response))
        elif user_input == "กลับไปเลือกหมวดหมู่":
            # Reset the state and show categories
            user_states[user_id] = {}
            show_interest_categories(event.reply_token)
        else:
            # Process the user's question with Ollama
            history_chat = user_state.get("chat_history", [])
            bot_response = llama_response(user_input, history_chat)
            history_chat.append(f"User: {user_input}")
            history_chat.append(f"Bot: {bot_response}")
            user_states[user_id]["chat_history"] = history_chat

            # Offer the user further interaction choices
            quick_reply_buttons = [
                QuickReplyButton(action=MessageAction(label="ถาม ollama อีกครั้ง", text="ถาม ollama")),
                QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่")),
            ]
            quick_reply = QuickReply(items=quick_reply_buttons)
            send_reply(event.reply_token, TextSendMessage(text=bot_response, quick_reply=quick_reply))

    elif user_input == "กลับไปเลือกหมวดหมู่":
        # Reset the state and show categories
        user_states[user_id] = {}
        show_interest_categories(event.reply_token)

    if user_input == "โปรโมชั่น":
        show_interest_categories(event.reply_token)
        bot_response = "โปรดเลือกหมวดหมู่ความสนใจ:"

    elif user_input == "แบรนด์":
        brands = fetch_all_brands()
        if brands:
            reply = "โปรดเลือกแบรนด์:"
            quick_reply_buttons = [QuickReplyButton(action=MessageAction(label=brand['name'], text=brand['name'])) for brand in brands]
            quick_reply_buttons.append(QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่")))
            quick_reply = QuickReply(items=quick_reply_buttons)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply, quick_reply=quick_reply))
            bot_response = reply
            user_states[user_id] = {"state": "SELECTING_BRAND"}
        else:
            bot_response = "ไม่พบแบรนด์"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=bot_response))
            user_states[user_id] = {}
            show_interest_categories(event.reply_token)

    elif user_input == "ส่วนลด":
        discounts = fetch_all_discounts()
        if discounts:
            reply = "โปรดเลือกส่วนลด:"
            quick_reply_buttons = [QuickReplyButton(action=MessageAction(label=f"{discount['discount']}", text=discount['discount'])) for discount in discounts]
            quick_reply_buttons.append(QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่")))
            quick_reply = QuickReply(items=quick_reply_buttons)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply, quick_reply=quick_reply))
            bot_response = reply
            user_states[user_id] = {"state": "SELECTING_DISCOUNT"}
        else:
            bot_response = "ไม่พบส่วนลด"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=bot_response))
            user_states[user_id] = {}
            show_interest_categories(event.reply_token)

    elif user_input == "คะแนน":
        reply = "โปรดเลือกการเรียงคะแนน:"
        quick_reply_buttons = [
            QuickReplyButton(action=MessageAction(label="น้อยไปมาก", text="น้อยไปมาก")),
            QuickReplyButton(action=MessageAction(label="มากไปน้อย", text="มากไปน้อย")),
            QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่")),
        ]
        quick_reply = QuickReply(items=quick_reply_buttons)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply, quick_reply=quick_reply))
        bot_response = reply
        user_states[user_id] = {"state": "SELECTING_RATING"}

    elif user_states.get(user_id, {}).get("state") == "SELECTING_BRAND":
        if user_input == "กลับไปเลือกหมวดหมู่":
            user_states[user_id] = {}
            show_interest_categories(event.reply_token)
        else:
            brand = user_input
            products = fetch_products_by_brand(brand)

            # ตรวจสอบว่าผลลัพธ์จาก fetch_products_by_brand() เป็นลิสต์หรือไม่
            if not isinstance(products, list):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เกิดข้อผิดพลาดในการดึงข้อมูลสินค้า"))
                return
            
            # ตรวจสอบว่าสินค้ามีอยู่จริงหรือไม่
            if products:
                # จัดเรียงสินค้าและแสดงผล
                sorted_products = sorted(products, key=lambda x: float(x['new_price'].replace('฿', '').replace(',', '').strip()))

                # ส่ง Flex Message สำหรับสินค้าของแบรนด์ที่เลือก
                send_flex_product_list(event, sorted_products, brand)
                
                # ประกาศตัวแปร quick_reply_buttons เป็นลิสต์
                quick_reply_buttons = []

                # ดึงรายชื่อแบรนด์ทั้งหมด
                all_brands = fetch_all_brands()

                # เพิ่มปุ่ม Quick Reply สำหรับแบรนด์อื่น ๆ ที่ไม่ใช่แบรนด์ที่ผู้ใช้เลือก
                for brand_data in all_brands:
                    if brand_data['name'] != brand:  # ไม่แสดงแบรนด์ที่เลือกแล้ว
                        quick_reply_buttons.append(QuickReplyButton(action=MessageAction(label=brand_data['name'], text=brand_data['name'])))
                
                # เพิ่มปุ่ม "กลับไปเลือกหมวดหมู่" ใน Quick Reply
                quick_reply_buttons.append(QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่")))

                quick_reply = QuickReply(items=quick_reply_buttons)

                # ส่ง Quick Reply แค่ครั้งเดียวหลังจากส่ง Flex Message ทั้งหมดเสร็จ
                line_bot_api.push_message(
                    event.source.user_id,
                    TextSendMessage(text="เลือกแบรนด์อื่น ๆ หรือตัวเลือกเพิ่มเติม:", quick_reply=quick_reply)
                )

                # อัปเดตสถานะผู้ใช้
                user_states[user_id]["state"] = "SELECTING_BRAND"
                user_states[user_id]["selected_brand"] = brand
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ไม่พบสินค้าจากแบรนด์ที่เลือก"))
                user_states[user_id] = {}
                show_interest_categories(event.reply_token)

    # State management for selected discount
    elif user_states.get(user_id, {}).get("state") == "SELECTING_DISCOUNT":
        if user_input == "กลับไปเลือกหมวดหมู่":
            user_states[user_id] = {}
            show_interest_categories(event.reply_token)
        else:
            discount = user_input
            products = fetch_products_by_discount(discount)
            sorted_products = sorted(products, key=lambda x: float(x['new_price'].replace('฿', '').replace(',', '').strip()))

            if products:
                # ใช้ Flex Message เพื่อแสดงสินค้า
                brand = products[0]['brand'] if 'brand' in products[0] else "ไม่ทราบแบรนด์"
                
                # ส่งสินค้าแบบ Flex Message
                send_flex_product_list(event, sorted_products, discount)

                # ดึงรายชื่อส่วนลดทั้งหมดและสร้างปุ่ม Quick Reply สำหรับส่วนลดอื่นๆ
                all_discounts = fetch_all_discounts()
                quick_reply_buttons = [
                    QuickReplyButton(action=MessageAction(label=discount_data['discount'], text=discount_data['discount']))
                    for discount_data in all_discounts if discount_data['discount'] != discount  # ไม่แสดงส่วนลดที่เลือกแล้ว
                ]

                # เพิ่มปุ่ม "กลับไปเลือกหมวดหมู่" ใน Quick Reply
                quick_reply_buttons.append(QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่")))

                quick_reply = QuickReply(items=quick_reply_buttons)

                # ส่ง Quick Reply แค่ครั้งเดียวหลังจากส่ง Flex Message ทั้งหมดเสร็จ
                line_bot_api.push_message(
                    event.source.user_id,
                    TextSendMessage(text="เลือกส่วนลดอื่น ๆ หรือตัวเลือกเพิ่มเติม:", quick_reply=quick_reply)
                )

                # บันทึกสถานะผู้ใช้
                user_states[user_id]["state"] = "SELECTING_DISCOUNT"
                user_states[user_id]["selected_discount"] = discount  # เก็บส่วนลดที่เลือกไว้
            else:
                bot_response = "ไม่พบสินค้าที่มีส่วนลดนี้"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=bot_response))
                user_states[user_id] = {}
                show_interest_categories(event.reply_token)

    elif user_states.get(user_id, {}).get("state") == "SELECTING_RATING":
        if user_input == "กลับไปเลือกหมวดหมู่":
            user_states[user_id] = {}
            show_interest_categories(event.reply_token)
            bot_response = "โปรดเลือกหมวดหมู่ความสนใจ:"
        else:
            rating_order = user_input
            products = fetch_products_by_rating_order(rating_order)

            # ตรวจสอบว่าสินค้ามีอยู่จริงหรือไม่
            if products:
                # ถ้าผู้ใช้เลือก "มากไปน้อย" ให้ทำการจัดเรียงแบบมากไปน้อย
                if rating_order == "มากไปน้อย":
                    sorted_products = sorted(products, key=lambda x: (x['rating'], float(x['new_price'].replace('฿', '').replace(',', '').strip())), reverse=True)
                else:  # ถ้าเลือก "น้อยไปมาก"
                    sorted_products = sorted(products, key=lambda x: (x['rating'], float(x['new_price'].replace('฿', '').replace(',', '').strip())))

                # ใช้ Flex Message แสดงสินค้าที่เรียงตามคะแนน
                send_flex_product_list(event, sorted_products, rating_order)

                # สร้างปุ่ม Quick Reply สำหรับการเรียงคะแนน
                ratings = ["น้อยไปมาก", "มากไปน้อย"]
                quick_reply_buttons = []

                # เพิ่มปุ่มสำหรับการเรียงลำดับคะแนนที่ไม่ได้เลือก
                for rating in ratings:
                    if rating != rating_order:  # ไม่แสดงตัวเลือกที่ถูกเลือกแล้ว
                        quick_reply_buttons.append(QuickReplyButton(action=MessageAction(label=rating, text=rating)))

                # เพิ่มปุ่ม "กลับไปเลือกหมวดหมู่" ใน Quick Reply
                quick_reply_buttons.append(QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่")))

                quick_reply = QuickReply(items=quick_reply_buttons)

                # ส่ง Quick Reply หลังจากส่ง Flex Message
                line_bot_api.push_message(
                    event.source.user_id,
                    TextSendMessage(text="เลือกการเรียงคะแนนอื่น ๆ หรือตัวเลือกเพิ่มเติม:", quick_reply=quick_reply)
                )

                # อัปเดตสถานะผู้ใช้
                user_states[user_id]["state"] = "SELECTING_RATING"
                user_states[user_id]["selected_rating"] = rating_order  # เก็บการเรียงคะแนนที่เลือกไว้
            else:
                bot_response = "ไม่พบสินค้าที่มีคะแนนนี้"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=bot_response))
                user_states[user_id] = {}
                show_interest_categories(event.reply_token)

    # Handling the "รายละเอียด" command
    elif user_input == "รายละเอียด":
        products = fetch_all_products()  # Fetch all products from Neo4j

        # Ensure that the user_id key exists in user_states
        if user_id not in user_states:
            user_states[user_id] = {}

        # Save products to user state for further usage
        user_states[user_id]['products'] = products

        # Show the first 10 products as quick reply
        show_products_quick_reply(event, products)

    # Handling the "สินค้าเพิ่มเติม" command
    elif user_input == "สินค้าเพิ่มเติม":
        products = user_states[user_id].get('products', [])
        
        # Show the next 10 products (if any)
        show_products_quick_reply(event, products[10:])

    # Handling product selection and showing detailed information
    elif user_input.startswith("รายละเอียด"):
        selected_product_title = user_input.replace("รายละเอียด ", "")
        product_info = fetch_product_details(selected_product_title)

        if product_info:
            # สร้าง Flex Message สำหรับรายละเอียดสินค้า
            product_flex = create_product_detail_flex(product_info)
            flex_message = FlexSendMessage(
                alt_text=f"รายละเอียดสินค้า {product_info['title']}",
                contents=product_flex
            )
            # ส่ง Flex Message
            line_bot_api.reply_message(event.reply_token, flex_message)

            # อัปเดตรายการสินค้าที่เหลือ
            remaining_products = [p for p in user_states[user_id]['products'] if p['title'] != selected_product_title]
            user_states[user_id]['products'] = remaining_products  # บันทึกสินค้าที่เหลือ

            # แสดงสินค้าเพิ่มเติมเป็น quick reply (ถ้ามี)
            show_remaining_products(event.source.user_id, remaining_products)

    # การจัดการสำหรับคำสั่ง "สินค้าเพิ่มเติม"
    elif user_input == "สินค้าเพิ่มเติม":
        products = user_states[user_id].get('products', [])

        # แสดงสินค้าถัดไป
        show_products_quick_reply(event, products[10:])

    # การจัดการสำหรับคำสั่ง "กลับไปเลือกหมวดหมู่"
    elif user_input == "กลับไปเลือกหมวดหมู่":
        user_states[user_id] = {}
        show_interest_categories(event.reply_token)
        bot_response = "โปรดเลือกหมวดหมู่ความสนใจ:"

    # Log the chat history
    log_chat_history(user_id, user_name, user_input, bot_response)

def show_interest_categories(reply_token):
    reply = "โปรดเลือกหมวดหมู่ความสนใจ:"
    quick_reply_buttons = [
        QuickReplyButton(action=MessageAction(label="แบรนด์", text="แบรนด์")),
        QuickReplyButton(action=MessageAction(label="ส่วนลด", text="ส่วนลด")),
        QuickReplyButton(action=MessageAction(label="คะแนน", text="คะแนน")),
        QuickReplyButton(action=MessageAction(label="รายละเอียด", text="รายละเอียด")),
        QuickReplyButton(action=MessageAction(label="ถาม Ollama", text="ถาม ollama"))
    ]
    quick_reply = QuickReply(items=quick_reply_buttons)
    try:
        line_bot_api.reply_message(reply_token, TextSendMessage(text=reply, quick_reply=quick_reply))
    except LineBotApiError as e:
        if e.status_code == 400:
            print("Failed to send message: Invalid reply token or token expired.")
        else:
            raise

def show_remaining_products(user_id, products):
    # Prepare the quick reply buttons
    quick_reply_buttons = []

    # Show up to 10 products as quick reply buttons
    for product in products[:10]:
        short_title = shorten_label(product['title'])
        quick_reply_buttons.append(
            QuickReplyButton(action=MessageAction(label=short_title, text=f"รายละเอียด {product['title']}"))
        )

    # If there are more than 10 products, add a "สินค้าเพิ่มเติม" button
    if len(products) > 10:
        quick_reply_buttons.append(
            QuickReplyButton(action=MessageAction(label="สินค้าเพิ่มเติม", text="สินค้าเพิ่มเติม"))
        )

    # Add a "กลับไปเลือกหมวดหมู่" button
    quick_reply_buttons.append(
        QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่"))
    )

    # Send the quick reply to the user using push_message
    quick_reply = QuickReply(items=quick_reply_buttons)
    line_bot_api.push_message(user_id, TextSendMessage(text="เลือกสินค้าอื่น หรือ กลับไปเลือกหมวดหมู่", quick_reply=quick_reply))

# Function to show products as quick replies with up to 10 buttons
def show_products_quick_reply(event, products):
    quick_reply_buttons = []

    # Show the first 10 products as quick reply buttons
    for product in products[:10]:
        quick_reply_buttons.append(
            QuickReplyButton(action=MessageAction(label=shorten_label(product['title']), text=f"รายละเอียด {product['title']}"))
        )

    # If there are more than 10 products, add a "สินค้าเพิ่มเติม" button
    if len(products) > 10:
        quick_reply_buttons.append(
            QuickReplyButton(action=MessageAction(label="สินค้าเพิ่มเติม", text="สินค้าเพิ่มเติม"))
        )
    
    # Add a "กลับไปเลือกหมวดหมู่" button
    quick_reply_buttons.append(
        QuickReplyButton(action=MessageAction(label="กลับไปเลือกหมวดหมู่", text="กลับไปเลือกหมวดหมู่"))
    )

    # Send quick reply message with the first 10 products
    quick_reply = QuickReply(items=quick_reply_buttons)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="โปรดเลือกสินค้า:", quick_reply=quick_reply))

def fetch_all_products():
    query = '''
    MATCH (p:Product)
    RETURN p.title AS title
    '''
    result = run_query(query)
    if result:
        return result
    return []

# Fetch all brands from Neo4j
def fetch_all_brands():
    query = '''
    MATCH (b:Brand)
    RETURN b.name AS name
    '''
    return run_query(query)

# Fetch all discounts from Neo4j
def fetch_all_discounts():
    query = '''
    MATCH (p:Product)
    RETURN DISTINCT p.discount AS discount
    '''
    return run_query(query)

# Fetch products by brand from Neo4j
def fetch_products_by_brand(brand):
    query = '''
    MATCH (b:Brand {name: $brand})-[:SELLS]->(p:Product)
    RETURN b.name AS brand, 
           p.title AS title, 
           p.new_price AS new_price, 
           p.discount AS discount, 
           p.rating AS rating, 
           p.old_price AS old_price, 
           p.variants AS variants,
           p.full_link AS full_link,
           p.image_url AS image_url
    '''
    return run_query(query, {'brand': brand})

# Fetch products by discount from Neo4j
def fetch_products_by_discount(discount):
    query = '''
    MATCH (b:Brand)-[:SELLS]->(p:Product)
    WHERE p.discount = $discount
    RETURN b.name AS brand, 
           p.title AS title, 
           p.new_price AS new_price, 
           p.discount AS discount, 
           p.rating AS rating, 
           p.old_price AS old_price, 
           p.variants AS variants,
           p.full_link AS full_link,
           p.image_url AS image_url
    '''
    return run_query(query, {'discount': discount})

# Fetch products by rating from Neo4j
def fetch_products_by_rating_order(order):
    query = f'''
    MATCH (b:Brand)-[:SELLS]->(p:Product)
    RETURN b.name AS brand, 
           p.title AS title, 
           p.new_price AS new_price, 
           p.discount AS discount, 
           p.rating AS rating, 
           p.old_price AS old_price, 
           p.variants AS variants,
           p.full_link AS full_link,
           p.image_url AS image_url
    ORDER BY p.rating {"ASC" if order == "น้อยไปมาก" else "DESC"}
    '''
    return run_query(query)

def fetch_product_details(title):
    query = '''
    MATCH (b:Brand)-[:SELLS]->(p:Product {title: $title})
    RETURN p.title AS title, 
           b.name AS brand, 
           p.old_price AS old_price,
           p.new_price AS new_price, 
           p.discount AS discount, 
           p.variants AS variants,
           p.rating AS rating, 
           p.description AS description,
           p.benefits AS benefits, 
           p.ingredients AS ingredients,
           p.how_to_use AS how_to_use,
           p.benefits_list AS benefits_list, 
           p.product_claims AS product_claims,
           p.set_contents AS set_contents,
           p.facts AS facts,
           p.full_link AS full_link,
           p.image_url AS image_url
    '''
    result = run_query(query, {'title': title})
    if result:
        return result[0]
    return None

# Functions to show quick replies after selecting brands, discounts, or ratings
def show_brands_reply(reply_token, brand):
    reply = f"คุณเลือกแบรนด์: {brand}"
    line_bot_api.reply_message(reply_token, TextSendMessage(text=reply))

def show_discounts_reply(reply_token, discount):
    reply = f"คุณเลือกส่วนลด: {discount}"
    line_bot_api.reply_message(reply_token, TextSendMessage(text=reply))

def show_ratings_reply(reply_token, rating_order):
    reply = f"คุณเลือกการเรียงคะแนน: {rating_order}"
    line_bot_api.reply_message(reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)