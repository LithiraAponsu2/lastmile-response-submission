/* Public area status page: find your area, see official warnings, services and safety centres.
   Read-only, no login, no personal data. Location lookup runs only in this browser. */
import { SECTOR_ORDER, STATUS, WARNING, areaName, areaState, drawStatusMap, legend, loadAreas, locate } from "/static/status-map.js";
const publicLayer = async meta => { const r = await fetch(`/api/public/layers/${meta.id}`); if (!r.ok) throw new Error("Layer unavailable"); return r.json(); };

const $ = s => document.querySelector(s);
const params = new URLSearchParams(location.search);
const LANGS = { en: "English", si: "සිංහල", ta: "தமிழ்" };
// Sinhala and Tamil wording is a prototype translation; have it reviewed by native speakers before real use.
const TEXT = {
  en: {
    title: "Sri Lanka disaster status", exercise: "Prototype exercise: every warning, status and figure here is fictional. Follow instructions from the DMC and local officials.",
    emergency: "Emergency numbers", call_dmc: "Disaster Management Centre", call_police: "Police", call_fire: "Fire & rescue", call_ambulance: "Suwa Seriya ambulance",
    search_label: "Search for a DS division or district", search: "DS division or district, e.g. Kothmale", use_location: "Use my location",
    location_note: "Your location never leaves your browser.", locating: "Finding your area…", not_found: "That location is outside the mapped areas.", no_geo: "Location is not available in this browser. Search by name instead.",
    no_match: "No area matches. Try the DS division or district name.", overview: "Across Sri Lanka", map_note: "Outlines are reference DS-division boundaries, not certified warning zones. A blank area means nothing was reported, not that it is safe.",
    warnings_all: "Official warnings in force", incidents: "Ongoing incidents", footer: "This page shows only what responding organisations chose to publish. It is not an official warning channel. Updates appear in the language they were recorded in.",
    staff: "Staff workspace", updated: "Updated", warnings: "Warnings in force", no_warning: "No warning recorded for this area. That is not an all-clear: follow official instructions.",
    services: "Services", no_report: "No report", centres: "Nearby safety centres", no_centres: "No published safety centre nearby.", free: "free places", full: "Full", places_unknown: "Free places not known",
    km: "km away", directions: "Open in map", affected: "Affected people", no_figures: "No figures published.", as_of: "As of", expected: "expected back", stale: "may be out of date",
    share: "Copy link to this area", copied: "Link copied", close: "Close", layer_warnings: "Warnings", layer_affected: "People affected", layer_services: "Services", map_shows: "Map shows",
    total_warnings: "warnings in force", total_areas: "areas with a service down", total_people: "people affected (reported)", total_centres: "safety centres open",
    none_warnings: "No official warning is published right now. That is not an all-clear.", none_incidents: "No incident is published right now.", by: "Reported by", follow: "Follow the advice of the issuing authority and local officials.",
    whole_district: "District", pick_division: "Choose your DS division:", incident_areas: "areas",
    messages_all: "Messages to the public", messages_area: "Messages for this area", no_messages: "No message has been sent to the public yet.", kind_sms: "SMS", kind_press_release: "Press release", sent_to: "Sent to",
    listed_centres: "Listed safety centres", listed_note: "Places listed for this area. A listing does not mean a centre is open; follow official announcements.", places_standard: "places by standard",
    lead: "Official warnings, services and safety centres for your area, and a way to ask for help.", help_title: "Need help?",
    help_lead: "Tell responders what is happening, with photos and your location. It goes to the organisation that can help, and you can follow it.",
    ask_open: "Ask for help", help_note: "If a life is in danger, call 117 first.", track_saved: "Requests sent from this device are listed here.",
    ask_search: "Search for your area", ask_no_place: "Choose the area: search below or use your location.", your_location: "Your location", ask_here: "Ask for help in this area", find: "Check your area",
    ask_title: "Ask for help or report a problem", ask_note: "For anything life-threatening, call 117, 119, 110 or 1990 first. This form is not watched like an emergency line.",
    ask_what: "What is happening? Any language.", ask_photos: "Photos (up to 3, optional)", ask_where: "Where?", ask_phone: "Phone (optional, so responders can call you)",
    ask_consent: "Responders may call me about this request. My number is never shown publicly.", ask_send: "Send request", ask_sending: "Sending…", ask_need_text: "Describe what is happening.",
    ask_need_place: "Choose where: use your location, or pick your area above.", ask_need_consent: "Tick the box to let responders call you, or leave the phone empty.", ask_place: "Place", ask_failed: "Could not send. Check your connection and try again; for emergencies call 117.",
    ask_sent: "Request sent", ask_code: "Your tracking code", ask_keep: "Keep this code to see what happens next. It is saved on this device.", track_title: "Track a request", track_label: "Tracking code", track: "Track",
    track_unknown: "No request with that code.", stage_received: "Received", stage_review: "Being reviewed", stage_check: "Being checked", stage_closed: "Closed", stage_handled: "Being handled by", stage_way: "Help on the way", stage_done: "Done",
    question: "Responders ask", answer: "Answer", answer_send: "Send answer", answered: "Thank you. Your answer was added.",
    gauges: "River levels nearby", gauge_major: "Major flood", gauge_minor: "Minor flood", gauge_alert: "Alert level", gauge_normal: "Normal", rising: "rising", falling: "falling", step_free: "step-free entrance", exercise_place: "exercise",
  },
  si: {
    title: "ශ්‍රී ලංකා ආපදා තත්ත්වය", exercise: "පරීක්ෂණ අභ්‍යාසයකි: මෙහි සියලු අනතුරු ඇඟවීම්, තත්ත්වයන් සහ සංඛ්‍යා කල්පිතය. ආපදා කළමනාකරණ මධ්‍යස්ථානයේ සහ ප්‍රාදේශීය නිලධාරීන්ගේ උපදෙස් අනුගමනය කරන්න.",
    emergency: "හදිසි ඇමතුම් අංක", call_dmc: "ආපදා කළමනාකරණ මධ්‍යස්ථානය", call_police: "පොලීසිය", call_fire: "ගිනි නිවීම සහ බේරා ගැනීම", call_ambulance: "සුව සැරිය ගිලන් රථ",
    search_label: "ප්‍රාදේශීය ලේකම් කොට්ඨාසයක් හෝ දිස්ත්‍රික්කයක් සොයන්න", search: "ප්‍රා.ලේ. කොට්ඨාසය හෝ දිස්ත්‍රික්කය, උදා. Kothmale", use_location: "මගේ ස්ථානය භාවිත කරන්න",
    location_note: "ඔබේ ස්ථානය ඔබේ බ්‍රවුසරයෙන් පිටතට යවන්නේ නැත.", locating: "ඔබේ ප්‍රදේශය සොයමින්…", not_found: "එම ස්ථානය සිතියම්ගත ප්‍රදේශවලින් පිටත ය.", no_geo: "මෙම බ්‍රවුසරයේ ස්ථානය ලබා ගත නොහැක. නමින් සොයන්න.",
    no_match: "ගැළපෙන ප්‍රදේශයක් නැත. කොට්ඨාසයේ හෝ දිස්ත්‍රික්කයේ නම උත්සාහ කරන්න.", overview: "ශ්‍රී ලංකාව පුරා", map_note: "මායිම් යොමු සඳහා පමණි; සහතික කළ අනතුරු ඇඟවීම් කලාප නොවේ. හිස් ප්‍රදේශයක් යනු කිසිවක් වාර්තා නොවූ බවයි, එය ආරක්ෂිත බව නොවේ.",
    warnings_all: "බලපැවැත්වෙන නිල අනතුරු ඇඟවීම්", incidents: "දැනට පවතින සිදුවීම්", footer: "මෙම පිටුවේ ඇත්තේ ප්‍රතිචාර දක්වන ආයතන ප්‍රසිද්ධ කිරීමට තෝරාගත් දේ පමණි. මෙය නිල අනතුරු ඇඟවීම් නාලිකාවක් නොවේ. යාවත්කාලීන කිරීම් ඒවා සටහන් කළ භාෂාවෙන් පෙන්වයි.",
    staff: "කාර්ය මණ්ඩල අවකාශය", updated: "යාවත්කාලීන කළේ", warnings: "බලපැවැත්වෙන අනතුරු ඇඟවීම්", no_warning: "මෙම ප්‍රදේශයට අනතුරු ඇඟවීමක් වාර්තා වී නැත. එය ආරක්ෂිත බවට දැනුම්දීමක් නොවේ: නිල උපදෙස් අනුගමනය කරන්න.",
    services: "සේවා", no_report: "වාර්තාවක් නැත", centres: "ආසන්න ආරක්ෂිත මධ්‍යස්ථාන", no_centres: "ආසන්නයේ ප්‍රසිද්ධ කළ ආරක්ෂිත මධ්‍යස්ථානයක් නැත.", free: "හිස් ඉඩ", full: "පිරී ඇත", places_unknown: "හිස් ඉඩ නොදනී",
    km: "කි.මී. දුරින්", directions: "සිතියමේ විවෘත කරන්න", affected: "බලපෑමට ලක්වූවන්", no_figures: "සංඛ්‍යා ප්‍රසිද්ධ කර නැත.", as_of: "දිනට", expected: "යථා තත්ත්වයට පත්වීමට අපේක්ෂිත", stale: "යල් පැන ගොස් තිබිය හැක",
    share: "මෙම ප්‍රදේශයට සබැඳිය පිටපත් කරන්න", copied: "සබැඳිය පිටපත් විය", close: "වසන්න", layer_warnings: "අනතුරු ඇඟවීම්", layer_affected: "බලපෑමට ලක්වූවන්", layer_services: "සේවා", map_shows: "සිතියම පෙන්වන්නේ",
    total_warnings: "බලපැවැත්වෙන අනතුරු ඇඟවීම්", total_areas: "සේවාවක් ක්‍රියා විරහිත ප්‍රදේශ", total_people: "බලපෑමට ලක්වූවන් (වාර්තා වූ)", total_centres: "විවෘත ආරක්ෂිත මධ්‍යස්ථාන",
    none_warnings: "දැනට නිල අනතුරු ඇඟවීමක් ප්‍රසිද්ධ කර නැත. එය ආරක්ෂිත බවට දැනුම්දීමක් නොවේ.", none_incidents: "දැනට ප්‍රසිද්ධ කළ සිදුවීමක් නැත.", by: "වාර්තා කළේ", follow: "නිකුත් කළ ආයතනයේ සහ ප්‍රාදේශීය නිලධාරීන්ගේ උපදෙස් අනුගමනය කරන්න.",
    whole_district: "දිස්ත්‍රික්කය", pick_division: "ඔබේ ප්‍රාදේශීය ලේකම් කොට්ඨාසය තෝරන්න:", incident_areas: "ප්‍රදේශ",
    messages_all: "මහජනතාවට පණිවිඩ", messages_area: "මෙම ප්‍රදේශයට පණිවිඩ", no_messages: "මහජනතාවට තවම පණිවිඩයක් යවා නැත.", kind_sms: "කෙටි පණිවිඩය", kind_press_release: "මාධ්‍ය නිවේදනය", sent_to: "යැවූයේ",
    listed_centres: "ලැයිස්තුගත ආරක්ෂිත මධ්‍යස්ථාන", listed_note: "මෙම ප්‍රදේශය සඳහා ලැයිස්තුගත ස්ථාන. ලැයිස්තුගත වීමෙන් මධ්‍යස්ථානය විවෘත බව අදහස් නොවේ; නිල නිවේදන අනුගමනය කරන්න.", places_standard: "ප්‍රමිතිය අනුව ඉඩ",
    lead: "ඔබේ ප්‍රදේශයේ නිල අනතුරු ඇඟවීම්, සේවා සහ ආරක්ෂිත මධ්‍යස්ථාන, සහ උදව් ඉල්ලීමට ක්‍රමයක්.", help_title: "උදව් අවශ්‍යද?",
    help_lead: "සිදුවන්නේ කුමක්දැයි ඡායාරූප සහ ඔබේ ස්ථානය සමඟ ප්‍රතිචාර දක්වන්නන්ට කියන්න. එය උදව් කළ හැකි ආයතනයට යන අතර ඔබට එය ලුහුබැඳිය හැක.",
    ask_open: "උදව් ඉල්ලන්න", help_note: "ජීවිතයක් අනතුරේ නම් පළමුව 117 අමතන්න.", track_saved: "මෙම උපාංගයෙන් යැවූ ඉල්ලීම් මෙහි පෙන්වයි.",
    ask_search: "ඔබේ ප්‍රදේශය සොයන්න", ask_no_place: "ප්‍රදේශය තෝරන්න: පහතින් සොයන්න හෝ ඔබේ ස්ථානය භාවිත කරන්න.", your_location: "ඔබේ ස්ථානය", ask_here: "මෙම ප්‍රදේශය සඳහා උදව් ඉල්ලන්න", find: "ඔබේ ප්‍රදේශය පරීක්ෂා කරන්න",
    ask_title: "උදව් ඉල්ලන්න හෝ ගැටලුවක් වාර්තා කරන්න", ask_note: "ජීවිත අවදානමක් ඇත්නම් පළමුව 117, 119, 110 හෝ 1990 අමතන්න. මෙම පෝරමය හදිසි ඇමතුම් මාර්ගයක් ලෙස නිරීක්ෂණය නොකෙරේ.",
    ask_what: "සිදුවන්නේ කුමක්ද? ඕනෑම භාෂාවකින්.", ask_photos: "ඡායාරූප (උපරිම 3, අත්‍යවශ්‍ය නොවේ)", ask_where: "කොහේද?", ask_phone: "දුරකථන අංකය (අත්‍යවශ්‍ය නොවේ, ප්‍රතිචාර දක්වන්නන්ට ඔබව ඇමතීමට)",
    ask_consent: "මෙම ඉල්ලීම ගැන ප්‍රතිචාර දක්වන්නන්ට මාව ඇමතිය හැක. මගේ අංකය කිසිවිටෙක ප්‍රසිද්ධියේ පෙන්වන්නේ නැත.", ask_send: "ඉල්ලීම යවන්න", ask_sending: "යවමින්…", ask_need_text: "සිදුවන්නේ කුමක්දැයි විස්තර කරන්න.",
    ask_need_place: "ස්ථානය තෝරන්න: ඔබේ ස්ථානය භාවිත කරන්න, නැතහොත් ඉහතින් ඔබේ ප්‍රදේශය තෝරන්න.", ask_need_consent: "ඔබව ඇමතීමට අවසර දීමට කොටුව සලකුණු කරන්න, නැතහොත් දුරකථන අංකය හිස්ව තබන්න.", ask_place: "ස්ථානය", ask_failed: "යැවිය නොහැකි විය. සම්බන්ධතාවය පරීක්ෂා කර නැවත උත්සාහ කරන්න; හදිසි අවස්ථාවල 117 අමතන්න.",
    ask_sent: "ඉල්ලීම යැව්වා", ask_code: "ඔබේ ලුහුබැඳීමේ කේතය", ask_keep: "ඊළඟට සිදුවන දේ බැලීමට මෙම කේතය තබා ගන්න. එය මෙම උපාංගයේ සුරැකේ.", track_title: "ඉල්ලීමක් ලුහුබඳින්න", track_label: "ලුහුබැඳීමේ කේතය", track: "ලුහුබඳින්න",
    track_unknown: "එම කේතයෙන් ඉල්ලීමක් නැත.", stage_received: "ලැබුණා", stage_review: "සලකා බලමින්", stage_check: "පරීක්ෂා කරමින්", stage_closed: "වසා ඇත", stage_handled: "භාරව සිටින්නේ", stage_way: "උදව් පැමිණෙමින්", stage_done: "අවසන්",
    question: "ප්‍රතිචාර දක්වන්නන්ගේ ප්‍රශ්නය", answer: "පිළිතුර", answer_send: "පිළිතුර යවන්න", answered: "ස්තූතියි. ඔබේ පිළිතුර එකතු කළා.",
    gauges: "ආසන්න ගංගා ජල මට්ටම්", gauge_major: "ප්‍රධාන ගංවතුර", gauge_minor: "සුළු ගංවතුර", gauge_alert: "අවදානම් මට්ටම", gauge_normal: "සාමාන්‍ය", rising: "ඉහළ යමින්", falling: "පහළ යමින්", step_free: "පඩි නොමැති ඇතුල්වීම", exercise_place: "අභ්‍යාස",
    sectors: { power: "විදුලිය", water: "ජල සැපයුම", roads: "මාර්ග සහ ප්‍රවේශය", health: "සෞඛ්‍ය සේවා", communications: "දුරකථන සහ අන්තර්ජාලය", shelter: "ආරක්ෂිත මධ්‍යස්ථාන", food: "ආහාර සහ සහන" },
    levels: { working: "ක්‍රියාත්මකයි", restoring: "යථා තත්ත්වයට පත් කරමින්", disrupted: "ක්‍රියා විරහිතයි", unknown: "නොදනී" },
    warning_levels: { yellow: "කහ · සුපරීක්ෂාකාරී වන්න", amber: "තැඹිලි · අවධානයෙන් සිටින්න, ඉවත් වීමට සූදානම් වන්න", red: "රතු · වහාම ඉවත් වන්න හෝ ක්‍රියා කරන්න" },
    hazards: { landslide: "නායයෑම්", flood: "ගංවතුර", rain: "තද වැසි", wind: "තද සුළං", cyclone: "සුළි කුණාටුව", tsunami: "සුනාමි", other: "වෙනත් අනතුර" },
    issuers: { nbro: "ජාතික ගොඩනැගිලි පර්යේෂණ සංවිධානය (NBRO)", met: "කාලගුණ විද්‍යා දෙපාර්තමේන්තුව", irrigation: "වාරිමාර්ග දෙපාර්තමේන්තුව", dmc: "ආපදා කළමනාකරණ මධ්‍යස්ථානය" },
    figures: { families_affected: "බලපෑමට ලක්වූ පවුල්", people_affected: "බලපෑමට ලක්වූ පුද්ගලයින්", deaths: "මරණ", injured: "තුවාල ලැබූවන්", missing: "අතුරුදන්", houses_destroyed: "සම්පූර්ණයෙන් විනාශ වූ නිවාස", houses_damaged: "අර්ධ වශයෙන් හානි වූ නිවාස", safety_centres: "විවෘත ආරක්ෂිත මධ්‍යස්ථාන", people_in_centres: "ආරක්ෂිත මධ්‍යස්ථානවල සිටින පුද්ගලයින්" },
  },
  ta: {
    title: "இலங்கை அனர்த்த நிலை", exercise: "முன்மாதிரிப் பயிற்சி: இங்குள்ள அனைத்து எச்சரிக்கைகள், நிலைகள் மற்றும் எண்ணிக்கைகள் கற்பனையானவை. அனர்த்த முகாமைத்துவ நிலையம் மற்றும் உள்ளூர் அதிகாரிகளின் அறிவுறுத்தல்களைப் பின்பற்றுங்கள்.",
    emergency: "அவசர இலக்கங்கள்", call_dmc: "அனர்த்த முகாமைத்துவ நிலையம்", call_police: "பொலிஸ்", call_fire: "தீயணைப்பு மற்றும் மீட்பு", call_ambulance: "சுவ செரிய நோயாளர் காவு வண்டி",
    search_label: "பிரதேச செயலகப் பிரிவு அல்லது மாவட்டத்தைத் தேடுங்கள்", search: "பிரதேச செயலகப் பிரிவு அல்லது மாவட்டம், எ.கா. Kothmale", use_location: "எனது இருப்பிடத்தைப் பயன்படுத்து",
    location_note: "உங்கள் இருப்பிடம் உங்கள் உலாவியை விட்டு வெளியே அனுப்பப்படாது.", locating: "உங்கள் பகுதியைத் தேடுகிறது…", not_found: "அந்த இடம் வரைபடப் பகுதிகளுக்கு வெளியே உள்ளது.", no_geo: "இந்த உலாவியில் இருப்பிடம் கிடைக்கவில்லை. பெயரால் தேடுங்கள்.",
    no_match: "பொருந்தும் பகுதி இல்லை. பிரிவு அல்லது மாவட்டப் பெயரை முயற்சிக்கவும்.", overview: "இலங்கை முழுவதும்", map_note: "எல்லைகள் குறிப்புக்கு மட்டுமே; சான்றளிக்கப்பட்ட எச்சரிக்கை வலயங்கள் அல்ல. வெற்றுப் பகுதி என்றால் எதுவும் அறிவிக்கப்படவில்லை என்பதே; அது பாதுகாப்பானது என்பதல்ல.",
    warnings_all: "நடைமுறையிலுள்ள உத்தியோகபூர்வ எச்சரிக்கைகள்", incidents: "நடைபெறும் அனர்த்தங்கள்", footer: "பதிலளிக்கும் நிறுவனங்கள் வெளியிடத் தேர்ந்தெடுத்தவை மட்டுமே இப்பக்கத்தில் உள்ளன. இது உத்தியோகபூர்வ எச்சரிக்கை வழி அல்ல. புதுப்பிப்புகள் அவை பதிவுசெய்யப்பட்ட மொழியில் காட்டப்படும்.",
    staff: "பணியாளர் பணியிடம்", updated: "புதுப்பிக்கப்பட்டது", warnings: "நடைமுறையிலுள்ள எச்சரிக்கைகள்", no_warning: "இப்பகுதிக்கு எச்சரிக்கை எதுவும் பதிவு செய்யப்படவில்லை. இது பாதுகாப்பானது என்ற அறிவிப்பு அல்ல: உத்தியோகபூர்வ அறிவுறுத்தல்களைப் பின்பற்றுங்கள்.",
    services: "சேவைகள்", no_report: "அறிக்கை இல்லை", centres: "அருகிலுள்ள பாதுகாப்பு நிலையங்கள்", no_centres: "அருகில் வெளியிடப்பட்ட பாதுகாப்பு நிலையம் இல்லை.", free: "காலி இடங்கள்", full: "நிரம்பியுள்ளது", places_unknown: "காலி இடங்கள் தெரியவில்லை",
    km: "கி.மீ. தொலைவில்", directions: "வரைபடத்தில் திற", affected: "பாதிக்கப்பட்டோர்", no_figures: "எண்ணிக்கைகள் வெளியிடப்படவில்லை.", as_of: "இன்றைய நிலவரப்படி", expected: "மீண்டும் இயங்க எதிர்பார்க்கப்படுவது", stale: "காலாவதியானதாக இருக்கலாம்",
    share: "இப்பகுதிக்கான இணைப்பை நகலெடு", copied: "இணைப்பு நகலெடுக்கப்பட்டது", close: "மூடு", layer_warnings: "எச்சரிக்கைகள்", layer_affected: "பாதிக்கப்பட்டோர்", layer_services: "சேவைகள்", map_shows: "வரைபடம் காட்டுவது",
    total_warnings: "நடைமுறையிலுள்ள எச்சரிக்கைகள்", total_areas: "சேவை தடைப்பட்ட பகுதிகள்", total_people: "பாதிக்கப்பட்டோர் (அறிவிக்கப்பட்டது)", total_centres: "திறந்துள்ள பாதுகாப்பு நிலையங்கள்",
    none_warnings: "தற்போது உத்தியோகபூர்வ எச்சரிக்கை எதுவும் வெளியிடப்படவில்லை. இது பாதுகாப்பானது என்ற அறிவிப்பு அல்ல.", none_incidents: "தற்போது வெளியிடப்பட்ட அனர்த்தம் எதுவும் இல்லை.", by: "அறிவித்தது", follow: "வெளியிட்ட நிறுவனம் மற்றும் உள்ளூர் அதிகாரிகளின் ஆலோசனையைப் பின்பற்றுங்கள்.",
    whole_district: "மாவட்டம்", pick_division: "உங்கள் பிரதேச செயலகப் பிரிவைத் தேர்ந்தெடுக்கவும்:", incident_areas: "பகுதிகள்",
    messages_all: "பொதுமக்களுக்கான செய்திகள்", messages_area: "இப்பகுதிக்கான செய்திகள்", no_messages: "பொதுமக்களுக்கு இதுவரை செய்தி எதுவும் அனுப்பப்படவில்லை.", kind_sms: "குறுஞ்செய்தி", kind_press_release: "ஊடக அறிக்கை", sent_to: "அனுப்பப்பட்டது",
    listed_centres: "பட்டியலிடப்பட்ட பாதுகாப்பு நிலையங்கள்", listed_note: "இப்பகுதிக்குப் பட்டியலிடப்பட்ட இடங்கள். பட்டியலில் இருப்பது நிலையம் திறந்துள்ளது என்று பொருளல்ல; அதிகாரபூர்வ அறிவிப்புகளைப் பின்பற்றவும்.", places_standard: "தர அளவின்படி இடங்கள்",
    lead: "உங்கள் பகுதிக்கான உத்தியோகபூர்வ எச்சரிக்கைகள், சேவைகள், பாதுகாப்பு நிலையங்கள், மற்றும் உதவி கேட்கும் வழி.", help_title: "உதவி தேவையா?",
    help_lead: "என்ன நடக்கிறது என்பதைப் புகைப்படங்கள் மற்றும் உங்கள் இருப்பிடத்துடன் பதிலளிப்பவர்களுக்குச் சொல்லுங்கள். அது உதவக்கூடிய நிறுவனத்திற்குச் செல்லும்; நீங்கள் அதைக் கண்காணிக்கலாம்.",
    ask_open: "உதவி கேளுங்கள்", help_note: "உயிருக்கு ஆபத்து என்றால் முதலில் 117 ஐ அழையுங்கள்.", track_saved: "இந்தச் சாதனத்திலிருந்து அனுப்பிய கோரிக்கைகள் இங்கே காட்டப்படும்.",
    ask_search: "உங்கள் பகுதியைத் தேடுங்கள்", ask_no_place: "பகுதியைத் தேர்ந்தெடுங்கள்: கீழே தேடுங்கள் அல்லது உங்கள் இருப்பிடத்தைப் பயன்படுத்துங்கள்.", your_location: "உங்கள் இருப்பிடம்", ask_here: "இந்தப் பகுதிக்கு உதவி கேளுங்கள்", find: "உங்கள் பகுதியைச் சரிபாருங்கள்",
    ask_title: "உதவி கேளுங்கள் அல்லது பிரச்சினையை அறிவியுங்கள்", ask_note: "உயிருக்கு ஆபத்தான நிலையில் முதலில் 117, 119, 110 அல்லது 1990 ஐ அழையுங்கள். இந்தப் படிவம் அவசர அழைப்பு வழியாகக் கண்காணிக்கப்படுவதில்லை.",
    ask_what: "என்ன நடக்கிறது? எந்த மொழியிலும்.", ask_photos: "புகைப்படங்கள் (அதிகபட்சம் 3, விருப்பத்தேர்வு)", ask_where: "எங்கே?", ask_phone: "தொலைபேசி (விருப்பத்தேர்வு, பதிலளிப்பவர்கள் உங்களை அழைக்க)",
    ask_consent: "இந்தக் கோரிக்கை தொடர்பாக பதிலளிப்பவர்கள் என்னை அழைக்கலாம். எனது இலக்கம் பொதுவில் ஒருபோதும் காட்டப்படாது.", ask_send: "கோரிக்கையை அனுப்பு", ask_sending: "அனுப்புகிறது…", ask_need_text: "என்ன நடக்கிறது என்று விவரியுங்கள்.",
    ask_need_place: "இடத்தைத் தேர்ந்தெடுங்கள்: உங்கள் இருப்பிடத்தைப் பயன்படுத்துங்கள், அல்லது மேலே உங்கள் பகுதியைத் தேர்ந்தெடுங்கள்.", ask_need_consent: "உங்களை அழைக்க அனுமதிக்க பெட்டியைத் தேர்வு செய்யுங்கள், அல்லது தொலைபேசியை வெறுமையாக விடுங்கள்.", ask_place: "இடம்", ask_failed: "அனுப்ப முடியவில்லை. இணைப்பைச் சரிபார்த்து மீண்டும் முயற்சிக்கவும்; அவசரத்திற்கு 117 ஐ அழையுங்கள்.",
    ask_sent: "கோரிக்கை அனுப்பப்பட்டது", ask_code: "உங்கள் கண்காணிப்புக் குறியீடு", ask_keep: "அடுத்து என்ன நடக்கிறது என்று பார்க்க இந்தக் குறியீட்டை வைத்திருங்கள். இது இந்தச் சாதனத்தில் சேமிக்கப்படும்.", track_title: "கோரிக்கையைக் கண்காணி", track_label: "கண்காணிப்புக் குறியீடு", track: "கண்காணி",
    track_unknown: "அந்தக் குறியீட்டில் கோரிக்கை இல்லை.", stage_received: "பெறப்பட்டது", stage_review: "பரிசீலிக்கப்படுகிறது", stage_check: "சரிபார்க்கப்படுகிறது", stage_closed: "மூடப்பட்டது", stage_handled: "கையாள்வது", stage_way: "உதவி வருகிறது", stage_done: "முடிந்தது",
    question: "பதிலளிப்பவர்களின் கேள்வி", answer: "பதில்", answer_send: "பதிலை அனுப்பு", answered: "நன்றி. உங்கள் பதில் சேர்க்கப்பட்டது.",
    gauges: "அருகிலுள்ள ஆற்று நீர்மட்டம்", gauge_major: "பெரும் வெள்ளம்", gauge_minor: "சிறு வெள்ளம்", gauge_alert: "எச்சரிக்கை நிலை", gauge_normal: "சாதாரணம்", rising: "உயர்கிறது", falling: "குறைகிறது", step_free: "படிகள் இல்லாத நுழைவு", exercise_place: "பயிற்சி",
    sectors: { power: "மின்சாரம்", water: "நீர் விநியோகம்", roads: "வீதிகள் மற்றும் அணுகல்", health: "சுகாதார சேவைகள்", communications: "தொலைபேசி மற்றும் இணையம்", shelter: "பாதுகாப்பு நிலையங்கள்", food: "உணவு மற்றும் நிவாரணம்" },
    levels: { working: "இயங்குகிறது", restoring: "சீரமைக்கப்படுகிறது", disrupted: "இயங்கவில்லை", unknown: "தெரியவில்லை" },
    warning_levels: { yellow: "மஞ்சள் · விழிப்புடன் இருங்கள்", amber: "செம்மஞ்சள் · எச்சரிக்கையாக இருங்கள், வெளியேறத் தயாராகுங்கள்", red: "சிவப்பு · உடனடியாக வெளியேறுங்கள் அல்லது செயல்படுங்கள்" },
    hazards: { landslide: "மண்சரிவு", flood: "வெள்ளம்", rain: "கடும் மழை", wind: "பலத்த காற்று", cyclone: "சூறாவளி", tsunami: "சுனாமி", other: "வேறு அபாயம்" },
    issuers: { nbro: "தேசிய கட்டிட ஆராய்ச்சி நிறுவனம் (NBRO)", met: "வளிமண்டலவியல் திணைக்களம்", irrigation: "நீர்ப்பாசனத் திணைக்களம்", dmc: "அனர்த்த முகாமைத்துவ நிலையம்" },
    figures: { families_affected: "பாதிக்கப்பட்ட குடும்பங்கள்", people_affected: "பாதிக்கப்பட்ட நபர்கள்", deaths: "மரணங்கள்", injured: "காயமடைந்தோர்", missing: "காணாமல் போனோர்", houses_destroyed: "முழுமையாக அழிந்த வீடுகள்", houses_damaged: "பகுதியளவில் சேதமடைந்த வீடுகள்", safety_centres: "திறந்துள்ள பாதுகாப்பு நிலையங்கள்", people_in_centres: "பாதுகாப்பு நிலையங்களில் உள்ளோர்" },
  },
};
const state = { lang: LANGS[params.get("lang")] ? params.get("lang") : (safeGet("status-lang") || "en"), layer: "warnings", area: params.get("area"), data: null, areas: null, ctl: null };
if (!LANGS[state.lang]) state.lang = "en";

function safeGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function safeSet(key, value) { try { localStorage.setItem(key, value); } catch { /* optional */ } }
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v); else if (k === "class") node.className = v; else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
const fill = (node, ...children) => node.replaceChildren(...children.filter(c => c != null && c !== false));
const t = key => TEXT[state.lang][key] ?? TEXT.en[key] ?? key;
// Server vocabulary is English; translated terms override it when available.
const vocab = () => {
  const base = state.data.vocabulary, tr = TEXT[state.lang];
  return Object.fromEntries(Object.entries(base).map(([k, v]) => [k, typeof v === "object" ? { ...v, ...(tr[k] || {}) } : v]));
};
const when = value => new Intl.DateTimeFormat(state.lang === "en" ? "en-GB" : state.lang === "si" ? "si-LK" : "ta-LK", { timeZone: "Asia/Colombo", dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
const toast = text => { const n = $("#toast"); n.textContent = text; n.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { n.hidden = true; }, 3000); };
const num = n => n.toLocaleString(state.lang === "en" ? "en-GB" : `${state.lang}-LK`);

function applyText() {
  document.documentElement.lang = state.lang;
  document.title = `${t("title")} · LastMile`;
  for (const node of document.querySelectorAll("[data-t]")) node.textContent = t(node.dataset.t);
  $("#area-search").placeholder = t("search");
  $("#ask-area").placeholder = t("search");
  $("#langs").replaceChildren(...Object.entries(LANGS).map(([code, label]) => el("button", { type: "button", lang: code, "aria-pressed": String(code === state.lang), onclick: () => { state.lang = code; safeSet("status-lang", code); syncUrl(); render(); } }, label)));
  $("#layers").setAttribute("aria-label", t("map_shows"));
  $("#layers").replaceChildren(...["warnings", "affected", "services"].map(k => el("button", { type: "button", "aria-pressed": String(state.layer === k), onclick: () => { state.layer = k; applyText(); drawLegend(); state.ctl?.update(null, { layer: k }); } }, t(`layer_${k}`))));
}
function syncUrl() {
  const next = new URLSearchParams();
  if (state.area) next.set("area", state.area);
  if (state.lang !== "en") next.set("lang", state.lang);
  history.replaceState(null, "", `${location.pathname}${next.toString() ? "?" + next : ""}`);
}
const active = w => !w.ended_at && (!w.valid_until || Date.parse(w.valid_until) > Date.parse(state.data.as_of)) && Date.parse(w.issued_at) <= Date.parse(state.data.as_of);
function distanceKm([lat1, lon1], [lat2, lon2]) {
  const r = Math.PI / 180, a = Math.sin((lat2 - lat1) * r / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin((lon2 - lon1) * r / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(a));
}

function renderTotals() {
  const pic = state.data, v = vocab(), states = Object.values(pic.areas);
  const values = [
    [pic.warnings.filter(active).length, t("total_warnings")],
    [states.filter(s => Object.values(s.services).some(r => r.level === "disrupted")).length, t("total_areas")],
    [pic.totals?.people_affected ?? states.reduce((n, s) => n + (s.figures?.values.people_affected || 0), 0), t("total_people")],
    [pic.centres.length, t("total_centres")],
  ];
  $("#totals").replaceChildren(...values.map(([n, label]) => el("div", { class: "total" }, el("strong", {}, num(n)), el("span", {}, label))));
  const live = pic.warnings.filter(active).sort((a, b) => WARNING[b.level].rank - WARNING[a.level].rank);
  $("#warnings").replaceChildren(...(live.length ? live.map(w => el("li", { class: `warning-item ${w.level}` },
    el("div", { class: "warning-head" }, el("span", { class: `warning-chip ${w.level}` }, el("span", { "aria-hidden": "true" }, WARNING[w.level].icon), `${v.warning_levels[w.level]} · ${v.hazards[w.hazard]}`), el("strong", {}, v.issuers[w.issuer])),
    el("p", {}, w.advice),
    el("div", { class: "area-links" }, w.areas.map(a => el("button", { type: "button", class: "link-button", onclick: () => a.startsWith("district-") ? chooseDivision(a, true) : select(a, true) },a.startsWith("district-") ? `${state.areas.byId.get(a)?.name} ${t("whole_district")}` : state.areas.byId.get(a)?.name || a))),
    el("small", { class: "muted" }, `${when(w.issued_at)}${w.valid_until ? ` → ${when(w.valid_until)}` : ""}`))) : [el("li", { class: "muted" }, t("none_warnings"))]));
  const messages = state.data.messages || [];
  $("#messages").replaceChildren(...(messages.length ? messages.slice(0, 6).map(messageItem) : [el("li", { class: "muted" }, t("no_messages"))]));
  $("#incidents").replaceChildren(...(pic.incidents.length ? pic.incidents.map(i => el("li", {}, el("strong", {}, i.title), el("span", { class: "muted" }, ` · ${i.district} · ${t("updated")} ${when(i.updated_at)}`))) : [el("li", { class: "muted" }, t("none_incidents"))]));
  $("#updated").textContent = `${t("updated")} ${when(pic.as_of)}`;
}
// Messages the coordinators sent: shown in the language they were written in.
const areaLabel = a => a.startsWith("district-") ? `${state.areas.byId.get(a)?.name || a} ${t("whole_district")}` : `${state.areas.byId.get(a)?.name || a} DS`;
const messageItem = m => el("li", { class: `message-item ${m.kind}` },
  el("div", { class: "warning-head" }, el("span", { class: "message-chip" }, t("kind_" + m.kind)), el("strong", {}, m.issuer)),
  m.subject && el("strong", { class: "message-subject", lang: m.language }, m.subject),
  el("p", { class: "message-body", lang: m.language }, m.text),
  el("small", { class: "muted" }, `${when(m.sent_at)}${m.areas.length ? ` · ${t("sent_to")}: ${m.areas.map(areaLabel).join("; ")}` : ""}`));
const divisionsOf = districtId => { const d = state.areas.byId.get(districtId); return state.areas.index.divisions.filter(x => x.district === d?.name).map(x => ({ id: x.id, label: `${x.name} DS`, sub: x.district })); };
// Area status is kept per DS division, and a district is never quietly narrowed to one of them: the person chooses.
function chooseDivision(districtId, focus = false) {
  $("#area-search").value = state.areas.byId.get(districtId)?.name || "";
  showMatches($("#area-results"), divisionsOf(districtId), m => { $("#area-search").value = m.label; select(m.id, true); }, t("pick_division"));
  if (focus) { $("#area-search").scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); $("#area-search").focus({ preventScroll: true }); }
}
function drawLegend() { $("#legend").replaceChildren(legend(state.layer, "all", vocab())); }

function renderArea() {
  const box = $("#area"), id = state.area;
  if (!id || !state.areas.byId.get(id)) { box.hidden = true; box.replaceChildren(); return; }
  const v = vocab(), s = areaState(state.data, id), info = state.areas.byId.get(id), top = s.warnings[0];
  const figureKeys = Object.keys(v.figures).filter(k => s.figures?.values?.[k] != null);
  const centres = state.data.centres.map(x => ({ ...x, km: distanceKm(info.centre, [x.latitude, x.longitude]) })).filter(x => x.km <= 30).sort((a, b) => a.km - b.km).slice(0, 4);
  const near = list => (list || []).map(x => ({ ...x, km: distanceKm(info.centre, [x.latitude, x.longitude]) })).sort((a, b) => a.km - b.km);
  const listed = near(state.places?.centres).filter(x => x.km <= 25).slice(0, 4), rivers = near(state.places?.gauges).filter(x => x.km <= 30).slice(0, 3);
  const districtId = state.areas.index.districts.find(d => d.name === info.district)?.id;
  const forArea = (state.data.messages || []).filter(m => m.kind === "sms" && m.areas.some(a => a === id || a === districtId)).slice(0, 3);
  box.hidden = false;
  box.className = `area ${top ? top.level : "none"}`;
  // Native replaceChildren would print a skipped (false) section as the word "false".
  fill(box,
    el("div", { class: "area-head" }, el("div", {}, el("h2", { id: "area-title", tabindex: "-1" }, areaName(state.areas, id)), el("small", { class: "muted" }, `${t("updated")} ${when(state.data.as_of)}`)),
      el("div", { class: "area-actions" }, el("button", { type: "button", class: "primary", onclick: () => openAsk(id) }, t("ask_here")), el("button", { type: "button", class: "quiet", onclick: async () => { syncUrl(); try { await navigator.clipboard.writeText(location.href); toast(t("copied")); } catch { toast(location.href); } } }, t("share")),
        el("button", { type: "button", class: "quiet", onclick: () => { state.area = null; syncUrl(); renderArea(); state.ctl?.select(null); $("#area-search").focus(); } }, t("close")))),
    el("section", { class: "block", "aria-labelledby": "area-warnings" }, el("h3", { id: "area-warnings" }, t("warnings")),
      s.warnings.length ? el("ul", { class: "plain-list" }, s.warnings.map(w => el("li", { class: `warning-item ${w.level}` },
        el("span", { class: `warning-chip big ${w.level}` }, el("span", { "aria-hidden": "true" }, WARNING[w.level].icon), `${v.warning_levels[w.level]} · ${v.hazards[w.hazard]}`),
        el("p", {}, w.advice), el("small", { class: "muted" }, `${v.issuers[w.issuer]} · ${when(w.issued_at)}${w.valid_until ? ` → ${when(w.valid_until)}` : ""}. ${t("follow")}`))))
        : el("p", { class: "calm" }, t("no_warning"))),
    forArea.length > 0 && el("section", { class: "block", "aria-labelledby": "area-messages" }, el("h3", { id: "area-messages" }, t("messages_area")), el("ul", { class: "message-list" }, forArea.map(messageItem))),
    el("section", { class: "block", "aria-labelledby": "area-services" }, el("h3", { id: "area-services" }, t("services")),
      el("ul", { class: "services" }, SECTOR_ORDER.map(k => { const r = s.services[k]; return el("li", { class: `service ${r?.level || "none"}` },
        el("span", { class: "service-icon", "aria-hidden": "true" }, r ? STATUS[r.level].icon : "–"),
        el("div", {}, el("strong", {}, v.sectors[k]), el("span", { class: "service-level" }, r ? v.levels[r.level] : t("no_report")),
          r?.note && el("p", {}, r.note),
          r && el("small", { class: "muted" }, `${when(r.at)}${r.expected_by ? ` · ${t("expected")} ${when(r.expected_by)}` : ""}${r.stale ? ` · ${t("stale")}` : ""}`))); }))),
    el("section", { class: "block", "aria-labelledby": "area-centres" }, el("h3", { id: "area-centres" }, t("centres")),
      centres.length ? el("ul", { class: "centres" }, centres.map(x => el("li", {},
        el("div", {}, el("strong", {}, x.name), el("span", { class: `places ${x.available == null ? "unknown" : x.available === 0 ? "full" : "free"}` }, x.available == null ? t("places_unknown") : x.available === 0 ? t("full") : `${num(x.available)} ${t("free")}`)),
        el("small", { class: "muted" }, `${x.km.toFixed(1)} ${t("km")} · `, el("a", { href: `https://www.openstreetmap.org/?mlat=${x.latitude}&mlon=${x.longitude}#map=15/${x.latitude}/${x.longitude}`, target: "_blank", rel: "noopener" }, t("directions"))))))
        : el("p", { class: "muted" }, t("no_centres"))),
    listed.length > 0 && el("section", { class: "block", "aria-labelledby": "area-listed" }, el("h3", { id: "area-listed" }, t("listed_centres")), el("p", { class: "muted" }, t("listed_note")),
      el("ul", { class: "centres" }, listed.map(x => el("li", {},
        el("div", {}, el("strong", {}, x.name.replace(" · fictional", "")), x.places_by_standard != null && el("span", { class: "places unknown" }, `${num(x.places_by_standard)} ${t("places_standard")}`)),
        el("small", { class: "muted" }, `${x.km.toFixed(1)} ${t("km")}${x.step_free ? ` · ${t("step_free")}` : ""} · `,
          el("a", { href: `https://www.openstreetmap.org/?mlat=${x.latitude}&mlon=${x.longitude}#map=15/${x.latitude}/${x.longitude}`, target: "_blank", rel: "noopener" }, t("directions"))))))),
    rivers.length > 0 && el("section", { class: "block", "aria-labelledby": "area-rivers" }, el("h3", { id: "area-rivers" }, t("gauges")),
      el("ul", { class: "centres" }, rivers.map(x => el("li", { class: `gauge ${x.state}` },
        el("div", {}, el("strong", {}, `${x.river} · ${x.name}`), el("span", { class: `places ${x.state === "normal" ? "free" : x.state === "alert" ? "unknown" : "full"}` }, t("gauge_" + x.state))),
        el("small", { class: "muted" }, `${x.level} ${x.unit}${x.trend && x.trend !== "steady" ? `, ${t(x.trend)}` : ""} · ${when(x.at)}${x.stale ? ` · ${t("stale")}` : ""} · ${x.km.toFixed(1)} ${t("km")}`))))),
    el("section", { class: "block", "aria-labelledby": "area-figures" }, el("h3", { id: "area-figures" }, t("affected")),
      figureKeys.length ? el("dl", { class: "figure-list" }, figureKeys.map(k => [el("dt", {}, v.figures[k]), el("dd", {}, num(s.figures.values[k]))])) : el("p", { class: "muted" }, t("no_figures")),
      figureKeys.length > 0 && el("small", { class: "muted" }, `${t("as_of")} ${when(Object.values(s.figures.as_of).sort().at(-1))}`)));
}
function select(id, focus = false) {
  if (!id) return;
  state.area = id; syncUrl(); renderArea();
  state.ctl?.select(id, { zoom: true });
  $("#area-results").hidden = true;
  if (focus) { $("#area").scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); $("#area-title")?.focus({ preventScroll: true }); }
}
function search() {
  showMatches($("#area-results"), areaMatches($("#area-search").value), m => { if (m.id.startsWith("district-")) chooseDivision(m.id); else { $("#area-search").value = m.label; select(m.id, true); } });
}
function locateMe() {
  const status = $("#locate-status");
  if (!navigator.geolocation) { status.textContent = t("no_geo"); return; }
  status.textContent = t("locating");
  navigator.geolocation.getCurrentPosition(pos => {
    const id = locate(state.areas, pos.coords.latitude, pos.coords.longitude);
    status.textContent = id ? "" : t("not_found");
    if (id) select(id, true);
  }, () => { status.textContent = t("no_geo"); }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
}
// ---------------------------------------------------------------- asking for help and tracking: two dialogs
const ask = { lat: null, lon: null, area: null };
const myCodes = () => { try { return JSON.parse(localStorage.getItem("my-requests") || "[]").filter(c => /^LM-[A-Z0-9]{6}$/.test(c)); } catch { return []; } };
const remember = code => { try { localStorage.setItem("my-requests", JSON.stringify([code, ...myCodes().filter(c => c !== code)].slice(0, 10))); } catch { /* optional */ } };
function askPlace() {
  const node = $("#ask-place"); if (!node) return;
  const text = ask.lat != null ? `${t("your_location")} (${ask.lat.toFixed(4)}, ${ask.lon.toFixed(4)})` : ask.area && state.areas ? areaName(state.areas, ask.area) : t("ask_no_place");
  node.textContent = text; node.classList.toggle("missing", ask.lat == null && !ask.area);
}
// Phone photos are large: shrink each to at most 1600 px as JPEG before sending.
async function shrink(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.8));
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = ""; for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return { base64: btoa(binary) };
}
const stageText = r => r.stage === "Done" ? t("stage_done") : r.stage === "Help on the way" ? t("stage_way") : r.body ? `${t("stage_handled")} ${r.body}` :
  ({ closed: t("stage_closed"), verify: t("stage_check"), received: t("stage_received") })[r.status] || t("stage_review");
function trackItem(r) {
  const item = el("li", { class: `track-item ${r.status}` }, el("div", {}, el("strong", { translate: "no" }, r.code), " · ", el("span", { class: "stage" }, stageText(r))),
    el("small", { class: "muted" }, `${r.area || ""}${r.area ? " · " : ""}${when(r.received_at)}${r.closed_reason ? ` · ${r.closed_reason}` : ""}`));
  if (r.question) {
    const box = el("textarea", { rows: 2, maxlength: 500, "aria-label": t("answer") });
    item.append(el("p", {}, el("strong", {}, `${t("question")}: `), r.question), box,
      el("button", { type: "button", onclick: async () => { const res = await fetch(`/api/public/requests/${r.code}/answer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: box.value }) });
        if (res.ok) { toast(t("answered")); showMine(); } } }, t("answer_send")));
  }
  return item;
}
async function track(code) {
  const res = await fetch(`/api/public/requests/${encodeURIComponent(code)}`, { cache: "no-store" });
  return res.ok ? res.json() : null;
}
async function showMine() {
  const found = (await Promise.all(myCodes().map(track))).filter(Boolean);
  $("#my-requests").replaceChildren(...found.map(trackItem));
}
// Area search shared by the page and the help dialog.
function areaMatches(text) {
  const q = text.trim().toLocaleLowerCase();
  if (q.length < 2 || !state.areas) return null;
  const all = [...state.areas.index.divisions.map(d => ({ id: d.id, label: `${d.name} DS`, sub: d.district, key: `${d.name} ${d.district}` })),
               ...state.areas.index.districts.map(d => ({ id: d.id, label: `${d.name} ${t("whole_district")}`, sub: "", key: d.name }))];
  return all.filter(a => a.key.toLocaleLowerCase().includes(q)).slice(0, 8);
}
function showMatches(list, matches, pick, heading = "") {
  if (matches == null) { list.hidden = true; return; }
  list.hidden = false;
  list.replaceChildren(...(heading ? [el("li", { class: "muted" }, heading)] : []), ...(matches.length ? matches.map(m => el("li", {}, el("button", { type: "button", onclick: () => pick(m) }, el("strong", {}, m.label), m.sub && el("span", { class: "muted" }, ` · ${m.sub}`)))) : [el("li", { class: "muted" }, t("no_match"))]));
}
function openAsk(area = state.area) {
  ask.area = area || null; ask.lat = ask.lon = null;
  $("#ask-form").hidden = false; $("#ask-done").hidden = true; $("#ask-status").textContent = ""; $("#ask-area").value = ""; $("#ask-area-results").hidden = true;
  askPlace();
  $("#ask-dialog").showModal();
  $("#ask-text").focus();
}
function openTrack() { $("#track-dialog").showModal(); showMine(); $("#track-code").focus(); }
function askSetup() {
  for (const button of document.querySelectorAll("[data-close]")) button.addEventListener("click", () => button.closest("dialog").close());
  $("#open-ask").addEventListener("click", () => openAsk());
  $("#open-track").addEventListener("click", openTrack);
  $("#ask-area").addEventListener("input", () => showMatches($("#ask-area-results"), areaMatches($("#ask-area").value), m => { ask.area = m.id; ask.lat = ask.lon = null; $("#ask-area").value = m.label; $("#ask-area-results").hidden = true; askPlace(); }));
  $("#ask-locate").addEventListener("click", () => {
    if (!navigator.geolocation) { $("#ask-status").textContent = t("no_geo"); return; }
    $("#ask-status").textContent = t("locating");
    navigator.geolocation.getCurrentPosition(pos => { ask.lat = pos.coords.latitude; ask.lon = pos.coords.longitude; ask.area = null; $("#ask-status").textContent = ""; askPlace(); },
      () => { $("#ask-status").textContent = t("no_geo"); }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  });
  $("#ask-form").addEventListener("submit", async event => {
    event.preventDefault();
    const status = $("#ask-status"), text = $("#ask-text").value.trim(), phone = $("#ask-phone").value.trim();
    if (text.length < 5) { status.textContent = t("ask_need_text"); $("#ask-text").focus(); return; }
    if (ask.lat == null && !ask.area) { status.textContent = t("ask_need_place"); $("#ask-area").focus(); return; }
    if (phone && !$("#ask-consent").checked) { status.textContent = t("ask_need_consent"); $("#ask-consent").focus(); return; }
    const send = $("#ask-send"); send.disabled = true; status.textContent = t("ask_sending");
    try {
      const photos = await Promise.all([...$("#ask-photos").files].slice(0, 3).map(shrink));
      const where = ask.lat != null ? { latitude: ask.lat, longitude: ask.lon } : { area: ask.area };
      const res = await fetch("/api/public/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, ...where, phone, consent: $("#ask-consent").checked, photos }) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { status.textContent = typeof body.detail === "string" ? body.detail : t("ask_failed"); return; }
      remember(body.code);
      $("#ask-form").reset(); $("#ask-form").hidden = true; status.textContent = "";
      fill($("#ask-done"), el("h3", {}, t("ask_sent")), el("p", {}, `${t("ask_code")}: `, el("strong", { class: "code", translate: "no" }, body.code)), el("p", { class: "muted" }, t("ask_keep")),
        el("ul", { class: "plain-list" }, trackItem(body)), el("div", { class: "dialog-actions" }, el("button", { type: "button", class: "primary", onclick: () => $("#ask-dialog").close() }, t("close"))));
      $("#ask-done").hidden = false; $("#ask-done").querySelector(".code").focus?.();
    } catch { status.textContent = t("ask_failed"); } finally { send.disabled = false; }
  });
  $("#track-form").addEventListener("submit", async event => {
    event.preventDefault();
    const code = $("#track-code").value.trim().toUpperCase();
    const found = /^LM-[A-Z0-9]{6}$/.test(code) ? await track(code) : null;
    if (!found) { toast(t("track_unknown")); return; }
    remember(code); showMine();
  });
  if (new URLSearchParams(location.search).get("track")) openTrack();
}
function render() {
  applyText();
  askPlace();
  if (!state.data) return;
  renderTotals(); drawLegend(); renderArea();
  state.ctl?.update(state.data, { vocab: vocab(), layer: state.layer });
}
async function load() {
  const r = await fetch("/api/public/status", { cache: "no-store" });
  if (!r.ok) throw new Error("Status unavailable");
  state.data = await r.json();
  // Listed places and river levels are optional extras; the page works without them.
  try { const p = await fetch("/api/public/places", { cache: "no-store" }); if (p.ok) state.places = await p.json(); } catch { state.places = null; }
  if (state.data.notice && state.lang === "en") TEXT.en.exercise = state.data.notice;
}
async function boot() {
  applyText();
  $("#area-search").addEventListener("input", search);
  $("#area-search").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); $("#area-results button")?.click(); } if (e.key === "Escape") $("#area-results").hidden = true; });
  $("#locate").addEventListener("click", locateMe);
  askSetup();
  try {
    [state.areas] = await Promise.all([loadAreas(), load()]);
  } catch (error) {
    $("#main").prepend(el("p", { class: "error", role: "alert" }, "The status could not load. Try again in a moment, and follow official instructions."));
    return;
  }
  render();
  if ($("#area-search").value.trim()) search();  // typed before the areas arrived
  state.ctl = await drawStatusMap($("#map"), state.data, { layer: state.layer, vocab: vocab(), selected: state.area, zoom: false, fetchLayer: publicLayer, label: "Sri Lanka map of published warnings, people affected and services", onSelect: id => select(id, true) });
  if (state.area) state.ctl?.select(state.area, { zoom: true });
  setInterval(async () => { if (document.hidden) return; try { await load(); render(); } catch { /* keep the last picture */ } }, 60000);
}
boot();
