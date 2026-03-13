import os
import json
from flask import Flask, request, jsonify, Response
from google.oauth2 import service_account
from googleapiclient.discovery import build
import anthropic

app = Flask(__name__)

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '1Y-BpCKJxuvWlAD22KORs5IMfrjk51Fcq63CEHoRGMtQ')
SHEET_NAME = os.environ.get('SHEET_NAME', 'Sheet1')


def get_sheets_service():
    creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
    )
    return build('sheets', 'v4', credentials=creds)


def get_as_records(product_code, color):
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{SHEET_NAME}!A2:T'
    ).execute()

    rows = result.get('values', [])
    records = []

    for row in rows:
        # 최소 5컬럼(증상까지) 있어야 유효한 데이터
        if len(row) < 5:
            continue

        row_product = row[2] if len(row) > 2 else ''
        row_color = row[3] if len(row) > 3 else ''

        # 제품코드 필터 (필수), 색상 필터 (입력된 경우만)
        if row_product.strip().upper() != product_code.strip().upper():
            continue
        if color and row_color.strip().upper() != color.strip().upper():
            continue

        parts = []
        for i in range(5):
            base = 5 + i * 3
            code = row[base] if base < len(row) else ''
            color_code = row[base + 1] if base + 1 < len(row) else ''
            qty = row[base + 2] if base + 2 < len(row) else ''
            if code:
                parts.append({'제품코드': code, '색상코드': color_code, '수량': qty})

        records.append({
            '접수날짜': row[0] if len(row) > 0 else '',
            '조치날짜': row[1] if len(row) > 1 else '',
            '제품코드': row_product,
            '색상': row_color,
            '증상': row[4] if len(row) > 4 else '',
            '부품': parts
        })

    return records


@app.route('/')
def index():
    html_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'index.html')
    with open(html_path, encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html')


@app.route('/api/recommend', methods=['POST'])
def recommend():
    data = request.get_json()
    product_code = (data.get('product_code') or '').strip()
    color = (data.get('color') or '').strip()
    symptoms = (data.get('symptoms') or '').strip()

    if not product_code or not symptoms:
        return jsonify({'error': '제품코드와 증상을 입력해주세요.'}), 400

    try:
        records = get_as_records(product_code, color)
    except Exception as e:
        return jsonify({'error': f'구글 시트 연결 오류: {str(e)}'}), 500

    if not records:
        msg = f'제품코드 {product_code}'
        if color:
            msg += f' / 색상 {color}'
        msg += '에 해당하는 AS 이력이 없습니다.'
        return jsonify({'error': msg}), 404

    records_text = json.dumps(records, ensure_ascii=False, indent=2)

    prompt = f"""당신은 AS(After Service) 부품 추천 전문가입니다.

과거 AS 조치 이력 (제품코드: {product_code}, 색상: {color or '전체'}):
{records_text}

현재 접수 증상:
{symptoms}

위 이력에서 현재 증상과 유사한 케이스를 분석하고, 필요한 부품을 추천해주세요.
색상이 명시되지 않은 경우 부품의 색상코드는 현재 제품 색상({color or '확인 필요'})을 참고하세요.

반드시 아래 JSON 형식으로만 응답하세요 (JSON 외 다른 텍스트 없이):
{{
  "similar_cases": [
    {{
      "증상": "과거 증상 설명",
      "유사도": "높음 또는 중간 또는 낮음",
      "조치날짜": "날짜"
    }}
  ],
  "recommended_parts": [
    {{
      "제품코드": "코드",
      "색상코드": "색상코드",
      "수량": "수량",
      "이유": "추천 이유"
    }}
  ],
  "분석": "전체적인 분석 요약"
}}"""

    try:
        client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
        message = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': prompt}]
        )
        response_text = message.content[0].text

        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        result = json.loads(response_text[start:end])
        return jsonify(result)

    except Exception as e:
        return jsonify({'error': f'AI 분석 오류: {str(e)}'}), 500
