/* ==========================================
   SafeRoute Delhi — local secrets template

   Copy this file to config.local.js (already gitignored) and fill in
   your real OpenRouteService key. config.local.js loads BEFORE
   config.js, so config.js's resolveOrsApiKey() picks this up
   automatically — no other file needs to change.

   Get a free key at: https://openrouteservice.org/dev/#/signup

   Don't have a key yet, or just testing? You can skip this file
   entirely and instead open the app with ?ors_key=YOUR_KEY appended
   to the URL — it'll be saved to localStorage for next time.
========================================== */

window.SAFEROUTE_ORS_KEY = "YOUR_OPENROUTESERVICE_API_KEY_HERE";
