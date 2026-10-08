/* Voice command parser: pure function, no DOM. Works in the browser (window.RBVoiceCommands) and in Node (tests).
   parse("gmail kholo") -> {type:"external", key:"gmail"} ; parse("weather dikhao") -> {type:"internal", key:"weather"} ;
   parse("ask chatgpt how to save tomato") -> {type:"chatgpt_ask", query:"how to save tomato"} ; anything else -> {type:"none"}. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.RBVoiceCommands = factory();
})(typeof self !== "undefined" ? self : this, function () {
  var OPEN_WORDS = ["open", "launch", "start", "go to", "goto", "show", "take me to", "kholo", "khol", "kholna", "khol do", "kholiye", "khol de", "kholein",
    "chalu", "chalao", "chala", "lagao", "laga do", "dikhao", "dikha", "dikhaiye", "dikha do", "jao", "jaao", "le chalo",
    "ओपन", "खोलो", "खोल", "खोलिए", "खोलना", "चालू", "चलाओ", "चला", "लगाओ", "दिखाओ", "दिखा", "जाओ", "शुरू", "खोलें"];
  var ASK_WORDS = ["ask", "pucho", "poocho", "puchho", "puch", "पूछो", "पूछ", "पूछें"];
  var TARGETS = [   // order = priority (more specific first)
    { key: "maps", type: "external", words: ["google maps", "maps", "map", "मैप", "मैप्स", "नक्शा", "नक्शे"] },
    { key: "chatgpt", type: "external", words: ["chatgpt", "chat gpt", "chat g p t", "gpt", "चैटजीपीटी", "चैट जीपीटी", "चैट जी पी टी"] },
    { key: "gemini", type: "external", words: ["gemini", "जेमिनी", "जैमिनी"] },
    { key: "gmail", type: "external", words: ["gmail", "g mail", "जीमेल", "जी मेल", "email", "e mail", "ईमेल", "mail", "मेल"] },
    { key: "youtube", type: "external", words: ["youtube", "you tube", "यूट्यूब", "यू ट्यूब"] },
    { key: "whatsapp", type: "external", words: ["whatsapp", "whats app", "व्हाट्सएप", "व्हाट्सऐप", "वॉट्सऐप", "वाट्सएप", "व्हाट्स ऐप"] },
    { key: "google", type: "external", words: ["google", "गूगल", "गुगल"] },
    { key: "weather", type: "internal", words: ["weather", "mausam", "मौसम"] },
    { key: "history", type: "internal", words: ["scan history", "history", "pichle scan", "purane scan", "इतिहास", "हिस्ट्री"] },
    { key: "scan", type: "internal", words: ["scan plant", "plant scan", "scan", "photo lo", "स्कैन", "पौधा स्कैन"] },
    { key: "fields", type: "internal", words: ["my fields", "fields", "field", "khet", "खेत", "मेरे खेत"] },
    { key: "community", type: "internal", words: ["community", "samuday", "कम्युनिटी"] },
    { key: "expert", type: "internal", words: ["plant hospital", "hospital", "expert", "doctor", "एक्सपर्ट", "विशेषज्ञ", "डॉक्टर"] },
    { key: "crops", type: "internal", words: ["my crops", "crops", "fasal", "फसल", "फसलें"] },
    { key: "iot", type: "internal", words: ["sensors", "sensor", "iot", "सेंसर"] },
    { key: "dashboard", type: "internal", words: ["dashboard", "डैशबोर्ड"] },
    { key: "notifications", type: "internal", words: ["notifications", "notification", "alerts", "सूचना", "नोटिफिकेशन"] }
  ];
  function normalize(t) {
    return String(t || "").toLowerCase().replace(/[.,!?;:"'()\[\]{}|।]/g, " ").replace(/\s+/g, " ").trim();
  }
  function has(text, phrase) {   // whole-word / whole-phrase match (works for Devanagari because words are space separated)
    return (" " + text + " ").indexOf(" " + phrase + " ") !== -1;
  }
  function parse(raw) {
    var t = normalize(raw);
    if (!t) return { type: "none" };
    var tokens = t.split(" "), n = tokens.length;
    var target = null;
    for (var i = 0; i < TARGETS.length && !target; i++)
      for (var j = 0; j < TARGETS[i].words.length; j++) if (has(t, TARGETS[i].words[j])) { target = TARGETS[i]; break; }
    if (!target) return { type: "none" };
    // "ask chatgpt <question>" / "chatgpt se pucho <question>": open ChatGPT with the question filled in
    if (target.key === "chatgpt") {
      var askAt = -1;
      for (var a = 0; a < ASK_WORDS.length; a++) if (has(t, ASK_WORDS[a])) { askAt = a; break; }
      if (askAt >= 0) {
        var rest = " " + t + " ";
        TARGETS[1].words.concat(ASK_WORDS, ["se", "ko", "to", "from", "ki", "mein", "me"]).sort(function (x, y) { return y.length - x.length; })
          .forEach(function (w) { rest = rest.split(" " + w + " ").join(" "); rest = rest.split(" " + w + " ").join(" "); });
        rest = rest.replace(/\s+/g, " ").trim();
        if (rest.split(" ").length >= 2) return { type: "chatgpt_ask", query: rest };
      }
    }
    var hasOpen = OPEN_WORDS.some(function (w) { return has(t, w); });
    var openEarly = OPEN_WORDS.some(function (w) { return has(tokens.slice(0, 3).join(" "), w) || has(tokens.slice(-2).join(" "), w); });
    if (n <= 3 || (hasOpen && (n <= 6 || openEarly))) return { type: target.type, key: target.key };
    return { type: "none" };
  }
  return { parse: parse, normalize: normalize };
});
