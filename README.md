# 🛒 AutoBill Buddy

Voice-powered grocery billing assistant for small shops. Speak your sales naturally and let AI handle the rest.

![Dashboard Preview](https://img.shields.io/badge/Status-Active-brightgreen)

## ✨ Features

- **🎙️ Voice Commands** - Say "Sold four kg rice and two tea" naturally
- **🧠 Smart Parser** - Handles voice errors ("Ford" → 4, "keji" → kg)
- **📊 Real-time Dashboard** - Track sales, inventory, and revenue
- **📅 Monthly Reports** - View daily breakdowns and trends
- **⚡ Instant Updates** - Stock updates automatically after each sale

## 🚀 Quick Start

### 1. Clone & Install
```bash
git clone https://github.com/saawant07/AutoBill-Buddy.git
cd AutoBill-Buddy
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file:
```env
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_key
GEMINI_API_KEY=your_gemini_api_key
```

### 3. Run
```bash
uvicorn main:app --reload
```
Open http://localhost:8000/static/index.html

## 🗣️ Voice Command Examples

| You Say | System Understands |
|---------|-------------------|
| "Sold four kg rice" | 4 kg Rice |
| "Ford milk and tree sugar" | 4 Milk, 3 Sugar |
| "Two bread, five eggs" | 2 Bread, 5 Eggs |

## 🛠️ Tech Stack

- **Backend**: FastAPI + Python
- **Database**: Supabase (PostgreSQL)
- **AI**: Google Gemini (fallback parser)
- **Frontend**: Vanilla HTML/CSS/JS

## 📝 License

MIT License - Built with ❤️ for hackathons
