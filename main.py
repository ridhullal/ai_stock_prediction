# full_app.py (with markdown support and app name)

from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from kiteconnect import KiteConnect
from openai import OpenAI
from dotenv import load_dotenv
import os
import datetime
import markdown2

# Load environment variables from .env file
load_dotenv()

# --- FastAPI app setup ---
app = FastAPI(title="AI Stock Analyser")
templates = Jinja2Templates(directory="templates")

# --- Load API Keys ---
ZERODHA_API_KEY = os.getenv("ZERODHA_API_KEY")
ZERODHA_API_SECRET = os.getenv("ZERODHA_API_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- Zerodha client ---
kite = KiteConnect(api_key=ZERODHA_API_KEY)
session_data = {}

# --- OpenAI client ---
client = OpenAI(api_key=OPENAI_API_KEY)

# Cache instruments for autocomplete
instrument_cache = []

@app.on_event("startup")
async def load_instruments():
    global instrument_cache
    try:
        instrument_cache = kite.instruments("NSE")
    except Exception as e:
        print("Failed to load instruments on startup. You'll need to login first.", e)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": "AI Stock Analyser"})

@app.get("/login")
async def login():
    login_url = kite.login_url()
    return RedirectResponse(login_url)

@app.get("/callback")
async def callback(request: Request):
    request_token = request.query_params.get("request_token")
    if not request_token:
        return {"error": "Missing request_token in callback"}

    try:
        data = kite.generate_session(request_token, api_secret=ZERODHA_API_SECRET)
        access_token = data["access_token"]
        session_data["access_token"] = access_token
        kite.set_access_token(access_token)
        global instrument_cache
        instrument_cache = kite.instruments("NSE")  # Reload instruments after login
        return RedirectResponse("/")
    except Exception as e:
        return {"error": str(e)}

@app.get("/symbols")
async def get_symbols(q: str = ""):
    if not instrument_cache:
        return JSONResponse(content=[])
    symbols = [inst["tradingsymbol"] for inst in instrument_cache if q.upper() in inst["tradingsymbol"]]
    return JSONResponse(content=symbols[:10])  # return top 10 matches

@app.post("/analyse", response_class=HTMLResponse)
async def analyse(request: Request,
                  stock: str = Form(...),
                  timeframe: str = Form(...),
                  start_date: str = Form(...),
                  end_date: str = Form(...)):
    try:
        if "access_token" not in session_data:
            return templates.TemplateResponse("result.html", {"request": request, "analysis_html": "<p><strong>Error:</strong> You must login first.</p>", "app_name": "AI Stock Analyser"})

        kite.set_access_token(session_data["access_token"])
        
        instrument_token = next((item['instrument_token'] for item in instrument_cache if item['tradingsymbol'] == stock), None)

        if not instrument_token:
            return templates.TemplateResponse("result.html", {"request": request, "analysis_html": "<p><strong>Error:</strong> Invalid stock symbol.</p>", "app_name": "AI Stock Analyser"})

        from_date = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        to_date = datetime.datetime.strptime(end_date, "%Y-%m-%d")

        historical_data = kite.historical_data(instrument_token, from_date, to_date, interval=timeframe)

        if not historical_data:
            return templates.TemplateResponse("result.html", {"request": request, "analysis_html": "<p><strong>Error:</strong> No historical data found.</p>", "app_name": "AI Stock Analyser"})

        prices_text = "\n".join([f"{d['date']}: Open={d['open']}, High={d['high']}, Low={d['low']}, Close={d['close']}" for d in historical_data])

        prompt = f"""
        Can you analyse the data and find some swing trading opportunity if available to make 5-10% profit?
        Note: it is Indian stock market. All prices are in rupees.

        {prices_text}
        """

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a financial trading analyst."},
                {"role": "user", "content": prompt}
            ]
        )

        answer = response.choices[0].message.content
        answer_html = markdown2.markdown(answer)

        return templates.TemplateResponse("result.html", {"request": request, "analysis_html": answer_html, "app_name": "AI Stock Analyser"})

    except Exception as e:
        return templates.TemplateResponse("result.html", {"request": request, "analysis_html": f"<p><strong>Error:</strong> {str(e)}</p>", "app_name": "AI Stock Analyser"})
