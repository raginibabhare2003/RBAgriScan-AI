# RBAgriScan Mobile + Voice + Chat Image Update

Changes:
- Mobile-first responsive polish for phones, tablets, and landscape mobile screens.
- Chatbot keeps existing voice input and text-to-speech assistance.
- Added a dedicated mobile camera button inside the chatbot.
- Chatbot image upload supports gallery/file picker plus camera capture.
- Existing /api/chat image FormData flow is preserved.
- Existing AI model, MySQL, PlantNet, Gemini, Plant Hospital, IoT, PWA and other functionality is not intentionally changed.

Run:
1. Activate the virtual environment.
2. `pip install -r requirements.txt`
3. `python app.py`
4. Open `http://127.0.0.1:5000`

For mobile camera/voice, allow browser camera and microphone permissions.
