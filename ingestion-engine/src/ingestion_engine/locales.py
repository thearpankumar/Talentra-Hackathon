"""Locale pools: multilingual person names (native script + latin transliteration), phone formats, phrases.

Names are written as "native|latin"; for latin-script locales the latin part is omitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _split(items: str) -> tuple[list[str], list[str]]:
    native, latin = [], []
    for it in items.split(","):
        it = it.strip()
        if not it:
            continue
        n, _, l = it.partition("|")
        native.append(n)
        latin.append(l or n)
    return native, latin


@dataclass(frozen=True)
class Locale:
    code: str
    country: str  # ISO-3166 alpha-2
    language: str  # ISO-639 code matching language.code
    script: str
    phone_cc: str  # without +
    phone_len: int  # national significant number length
    phone_first: str  # allowed leading digits
    first: list[str]
    first_latin: list[str]
    last: list[str]
    last_latin: list[str]
    family_first: bool  # family name written first (zh, ja)
    phrases: list[str] = field(default_factory=list)
    email_domains: tuple[str, ...] = ("gmail.com", "outlook.com", "yahoo.com", "proton.me")


def _mk(code, country, lang, script, cc, plen, pfirst, first, last, family_first=False, phrases=None, domains=None):
    f, fl = _split(first)
    l, ll = _split(last)
    kw = {}
    if domains:
        kw["email_domains"] = domains
    return Locale(code, country, lang, script, cc, plen, pfirst, f, fl, l, ll, family_first, phrases or [], **kw)


_EN = ["Experienced professional with a strong track record.", "Seeking new challenges and growth opportunities.",
       "Skilled in teamwork, communication and problem solving.", "Available for immediate joining."]

LOCALES: dict[str, Locale] = {}


def _add(loc: Locale) -> None:
    LOCALES[loc.code] = loc


_add(_mk("en_IN", "IN", "en", "Latin", "91", 10, "6789",
         "Aarav,Vihaan,Arjun,Rohan,Karan,Rahul,Amit,Sanjay,Priya,Ananya,Neha,Pooja,Kavya,Sneha,Divya,Ritu,Vikram,Suresh,Deepak,Meera,Ishaan,Aditya,Nisha,Swati,Manish,Rajesh,Anjali,Sunita,Harish,Lakshmi",
         "Sharma,Verma,Gupta,Singh,Kumar,Patel,Reddy,Iyer,Nair,Das,Mehta,Joshi,Rao,Pillai,Chatterjee,Banerjee,Kulkarni,Desai,Shetty,Menon,Yadav,Mishra,Pandey,Chauhan,Bhat",
         phrases=_EN + ["Strong in Java, Spring Boot and microservices.", "Fresher from a Tier-2 engineering college."]))
_add(_mk("hi_IN", "IN", "hi", "Devanagari", "91", 10, "6789",
         "आरव|Aarav,विहान|Vihaan,अर्जुन|Arjun,रोहन|Rohan,करण|Karan,राहुल|Rahul,अमित|Amit,प्रिया|Priya,अनन्या|Ananya,नेहा|Neha,पूजा|Pooja,काव्या|Kavya,स्नेहा|Sneha,दिव्या|Divya,विक्रम|Vikram,सुरेश|Suresh,दीपक|Deepak,मीरा|Meera",
         "शर्मा|Sharma,वर्मा|Verma,गुप्ता|Gupta,सिंह|Singh,कुमार|Kumar,पटेल|Patel,यादव|Yadav,मिश्रा|Mishra,पांडे|Pandey,चौहान|Chauhan,जोशी|Joshi,मेहता|Mehta",
         phrases=["अनुभवी पेशेवर, तुरंत जॉइन करने के लिए उपलब्ध।", "टीमवर्क और संवाद में कुशल।", "नई चुनौतियों की तलाश में हूँ।", "इलेक्ट्रीशियन के रूप में पाँच वर्ष का अनुभव।"]))
_add(_mk("ta_IN", "IN", "ta", "Tamil", "91", 10, "6789",
         "அருண்|Arun,கார்த்திக்|Karthik,சுரேஷ்|Suresh,விஜய்|Vijay,ராஜேஷ்|Rajesh,லட்சுமி|Lakshmi,மீனா|Meena,காவ்யா|Kavya,பிரியா|Priya,திவ்யா|Divya,முருகன்|Murugan,செல்வி|Selvi",
         "சுப்பிரமணியன்|Subramanian,ராமசாமி|Ramasamy,கிருஷ்ணன்|Krishnan,நாயர்|Nair,பிள்ளை|Pillai,ஐயர்|Iyer,முத்து|Muthu",
         phrases=["அனுபவம் வாய்ந்த தொழில்முறை நிபுணர்.", "உடனடியாக பணியில் சேர தயார்."]))
_add(_mk("bn_BD", "BD", "bn", "Bengali", "880", 10, "1",
         "রহিম|Rahim,করিম|Karim,সুমন|Sumon,জাহিদ|Zahid,ফাতেমা|Fatema,নুসরাত|Nusrat,তানভীর|Tanvir,মিজান|Mizan,আয়েশা|Ayesha,সাবরিনা|Sabrina",
         "ইসলাম|Islam,আহমেদ|Ahmed,হোসেন|Hossain,খান|Khan,চৌধুরী|Chowdhury,মিয়া|Mia,সরকার|Sarkar",
         phrases=["অভিজ্ঞ পেশাদার, দ্রুত যোগদানে প্রস্তুত।", "দলগত কাজে দক্ষ।"]))
_add(_mk("ur_PK", "PK", "ur", "Arabic", "92", 10, "3",
         "محمد|Muhammad,علی|Ali,احمد|Ahmed,حسن|Hassan,فاطمہ|Fatima,عائشہ|Ayesha,بلال|Bilal,عمر|Umar,زینب|Zainab,سارہ|Sara",
         "خان|Khan,شاہ|Shah,ملک|Malik,چوہدری|Chaudhry,قریشی|Qureshi,بٹ|Butt,انصاری|Ansari",
         phrases=["تجربہ کار پیشہ ور، فوری شمولیت کے لیے دستیاب۔", "ٹیم ورک میں ماہر۔"]))
_add(_mk("en_NG", "NG", "en", "Latin", "234", 10, "789",
         "Chinedu,Emeka,Ifeanyi,Obinna,Tunde,Ayo,Segun,Kelechi,Ngozi,Chiamaka,Funmilayo,Adaeze,Blessing,Chioma,Ibrahim,Musa,Amina,Fatima,Uche,Damilola",
         "Okafor,Adeyemi,Okonkwo,Balogun,Abubakar,Ibrahim,Eze,Nwosu,Bello,Ogunleye,Adebayo,Chukwu,Olawale,Musa,Onyeka,Akinola",
         phrases=_EN + ["Experienced freelance graphic designer based in Lagos.", "Open to remote and gig work."]))
_add(_mk("yo_NG", "NG", "yo", "Latin", "234", 10, "789",
         "Ṣọlá,Adéwálé,Ọlábísí,Bọ́lájí,Yẹmisi,Ọlámidé,Àdùnní,Fẹ́mi,Táíwò,Kẹ́hìndé,Ìbìkúnlé,Ayọ̀délé",
         "Adébáyọ̀,Ọlọ́unwa,Àjàyí,Ògúnlẹ́yẹ,Adéyẹmí,Olúwáṣeun,Babátúndé,Fáṣọlá,Ọlátúnjí,Akínọlá",
         phrases=["Oníṣẹ́ ọwọ́ tó ní ìrírí, ó ṣetán láti bẹ̀rẹ̀ iṣẹ́ lẹ́sẹ̀kẹsẹ̀."]))
_add(_mk("ar_AE", "AE", "ar", "Arabic", "971", 9, "5",
         "محمد|Mohammed,أحمد|Ahmed,خالد|Khalid,سلطان|Sultan,فاطمة|Fatima,مريم|Maryam,عائشة|Aisha,راشد|Rashid,حمدان|Hamdan,سعيد|Saeed,نورة|Noura,علي|Ali",
         "المنصوري|Al Mansoori,الكعبي|Al Kaabi,النعيمي|Al Nuaimi,الشامسي|Al Shamsi,الهاشمي|Al Hashimi,المهيري|Al Mheiri,الزعابي|Al Zaabi",
         phrases=["محترف ذو خبرة، متاح للانضمام فورا.", "أبحث عن فرص عمل في دبي وأبوظبي.", "خبرة في الخدمات اللوجستية والإدارة."]))
_add(_mk("ar_SA", "SA", "ar", "Arabic", "966", 9, "5",
         "عبدالله|Abdullah,فهد|Fahad,سعود|Saud,ناصر|Nasser,تركي|Turki,سارة|Sarah,نورة|Noura,هند|Hind,ريم|Reem,بدر|Badr,فيصل|Faisal,منال|Manal",
         "القحطاني|Al Qahtani,العتيبي|Al Otaibi,الدوسري|Al Dosari,الغامدي|Al Ghamdi,الشهري|Al Shehri,الحربي|Al Harbi,الزهراني|Al Zahrani",
         phrases=["خبرة في أعمال البناء والصيانة.", "متاح للعمل فورا في الرياض وجدة."]))
_add(_mk("ar_EG", "EG", "ar", "Arabic", "20", 10, "1",
         "محمود|Mahmoud,مصطفى|Mostafa,أحمد|Ahmed,كريم|Karim,منى|Mona,هبة|Heba,نهى|Noha,ياسر|Yasser,عمرو|Amr,دينا|Dina",
         "إبراهيم|Ibrahim,حسن|Hassan,عبد الله|Abdallah,السيد|El Sayed,فتحي|Fathy,سالم|Salem,رمضان|Ramadan",
         phrases=["مهندس برمجيات ذو خبرة.", "أبحث عن فرصة عمل عن بعد."]))
_add(_mk("en_PH", "PH", "fil", "Latin", "63", 10, "9",
         "Juan,Jose,Mark,John,Maria,Ana,Kristine,Angelica,Jericho,Joshua,Mary Grace,Rhea,Carlo,Dennis,Marites,Jomar",
         "Santos,Reyes,Cruz,Bautista,Ocampo,Garcia,Mendoza,Torres,Villanueva,Ramos,Aquino,Dela Cruz,Castillo,Navarro",
         phrases=_EN + ["Experienced in BPO customer support and healthcare services.", "Willing to work abroad."]))
_add(_mk("zh_CN", "CN", "zh", "Han", "86", 11, "1",
         "伟|Wei,芳|Fang,娜|Na,敏|Min,静|Jing,丽|Li,强|Qiang,磊|Lei,洋|Yang,艳|Yan,勇|Yong,杰|Jie,涛|Tao,明|Ming,秀英|Xiuying,超|Chao",
         "王|Wang,李|Li,张|Zhang,刘|Liu,陈|Chen,杨|Yang,黄|Huang,赵|Zhao,周|Zhou,吴|Wu,徐|Xu,孙|Sun",
         family_first=True,
         phrases=["经验丰富的专业人士，可立即入职。", "擅长团队合作与沟通。", "熟悉Java和Python开发。"]))
_add(_mk("ja_JP", "JP", "ja", "Han", "81", 10, "789",
         "太郎|Taro,花子|Hanako,健太|Kenta,美咲|Misaki,翔太|Shota,愛|Ai,大輔|Daisuke,結衣|Yui,拓海|Takumi,陽菜|Hina",
         "佐藤|Sato,鈴木|Suzuki,高橋|Takahashi,田中|Tanaka,伊藤|Ito,渡辺|Watanabe,山本|Yamamoto,中村|Nakamura,小林|Kobayashi",
         family_first=True, phrases=["経験豊富な専門家です。すぐに勤務可能です。", "チームワークに自信があります。"]))
_add(_mk("es_MX", "MX", "es", "Latin", "52", 10, "123456789",
         "José,Juan,Luis,Carlos,Miguel,María,Guadalupe,Fernanda,Valentina,Sofía,Diego,Alejandro,Ximena,Jesús,Andrés,Daniela",
         "Hernández,García,Martínez,López,González,Rodríguez,Pérez,Sánchez,Ramírez,Flores,Gómez,Díaz,Cruz,Morales",
         phrases=["Profesional con experiencia, disponible de inmediato.", "Habilidades en trabajo en equipo y comunicación."]))
_add(_mk("pt_BR", "BR", "pt", "Latin", "55", 11, "9",
         "João,Pedro,Lucas,Gabriel,Maria,Ana,Beatriz,Juliana,Camila,Rafael,Thiago,Larissa,Mateus,Fernanda,Letícia,Gustavo",
         "Silva,Santos,Oliveira,Souza,Rodrigues,Ferreira,Alves,Pereira,Lima,Gomes,Costa,Ribeiro,Martins,Carvalho",
         phrases=["Profissional experiente, disponível imediatamente.", "Habilidades em trabalho em equipe e comunicação."]))
_add(_mk("fr_FR", "FR", "fr", "Latin", "33", 9, "67",
         "Lucas,Louis,Hugo,Léa,Chloé,Camille,Manon,Thomas,Julien,Élodie,Mathieu,Océane,Antoine,Inès,Clément,Amélie",
         "Martin,Bernard,Dubois,Thomas,Robert,Richard,Petit,Durand,Leroy,Moreau,Simon,Laurent,Lefèbvre,Girard",
         phrases=["Professionnel expérimenté, disponible immédiatement.", "Compétences en travail d'équipe et communication."]))
_add(_mk("de_DE", "DE", "de", "Latin", "49", 10, "1",
         "Lukas,Leon,Finn,Jonas,Anna,Lena,Mia,Hannah,Felix,Maximilian,Sophie,Jürgen,Katharina,Tobias,Stefan,Ülkü",
         "Müller,Schmidt,Schneider,Fischer,Weber,Meyer,Wagner,Becker,Schulz,Hoffmann,Köhler,Bauer,Richter,Klein",
         phrases=["Erfahrener Fachmann, sofort verfügbar.", "Stark in Teamarbeit und Kommunikation."]))
_add(_mk("ru_RU", "RU", "ru", "Cyrillic", "7", 10, "9",
         "Александр|Aleksandr,Дмитрий|Dmitry,Иван|Ivan,Сергей|Sergey,Анна|Anna,Мария|Maria,Екатерина|Ekaterina,Ольга|Olga,Николай|Nikolay,Татьяна|Tatyana",
         "Иванов|Ivanov,Смирнов|Smirnov,Кузнецов|Kuznetsov,Попов|Popov,Соколов|Sokolov,Лебедев|Lebedev,Козлов|Kozlov,Новиков|Novikov",
         phrases=["Опытный специалист, готов приступить немедленно.", "Навыки командной работы и коммуникации."]))
_add(_mk("en_US", "US", "en", "Latin", "1", 10, "23456789",
         "James,John,Robert,Michael,Mary,Jennifer,Linda,Jessica,Emily,Daniel,Matthew,Ashley,Brandon,Tyler,Olivia,Ethan",
         "Smith,Johnson,Williams,Brown,Jones,Garcia,Miller,Davis,Wilson,Anderson,Taylor,Thomas,Moore,Jackson,Martin",
         phrases=_EN, domains=("gmail.com", "outlook.com", "yahoo.com", "icloud.com")))
_add(_mk("en_GB", "GB", "en", "Latin", "44", 10, "7",
         "Oliver,George,Harry,Jack,Amelia,Olivia,Isla,Poppy,Charlie,Thomas,Freddie,Sophie,Archie,Grace",
         "Smith,Jones,Taylor,Brown,Williams,Wilson,Evans,Thomas,Roberts,Walker,Wright,Robinson,Thompson,Hughes",
         phrases=_EN, domains=("gmail.com", "outlook.com", "hotmail.co.uk", "yahoo.co.uk")))
_add(_mk("sw_KE", "KE", "sw", "Latin", "254", 9, "17",
         "Juma,Baraka,Amani,Wanjiru,Otieno,Achieng,Kamau,Njeri,Mwangi,Wambui,Kiprop,Atieno,Omondi,Chebet",
         "Kamau,Otieno,Mwangi,Njoroge,Kipchoge,Odhiambo,Wanjala,Mutua,Kariuki,Ochieng,Maina,Kiprono",
         phrases=["Mtaalamu mwenye uzoefu, tayari kuanza kazi mara moja.", "Ujuzi wa kufanya kazi kwa timu."]))

SCRIPTS = sorted({loc.script for loc in LOCALES.values()})

COUNTRY_LOCALES: dict[str, list[str]] = {}
for _l in LOCALES.values():
    COUNTRY_LOCALES.setdefault(_l.country, []).append(_l.code)

COMPANY_SUFFIX = ["Technologies", "Solutions", "Staffing", "Logistics", "Industries", "Services", "Group", "Systems",
                  "Constructions", "Hospitality", "Healthcare", "Global", "Enterprises", "Labs", "Partners"]
COMPANY_STEM = ["Nova", "Apex", "Zenith", "Orion", "Bluewave", "Sunrise", "Prime", "Vertex", "Atlas", "Crest", "Delta",
                "Evergreen", "Falcon", "Granite", "Harbor", "Ignite", "Jade", "Keystone", "Lotus", "Meridian",
                "Northbridge", "Oasis", "Pinnacle", "Quantum", "Riverstone", "Summit", "Titan", "Unity", "Vista", "Zephyr"]
JOB_TITLES = ["Software Engineer", "Java Developer", "Data Analyst", "Graphic Designer", "UI/UX Designer", "Electrician",
              "Welder", "Heavy Vehicle Driver", "Security Guard", "Sous Chef", "Staff Nurse", "Warehouse Associate",
              "Housekeeping Attendant", "Accountant", "Sales Executive", "Customer Support Associate", "Teacher",
              "Project Manager", "DevOps Engineer", "Forklift Operator", "Mason", "Plumber", "Delivery Rider"]
SENIORITY = ["Junior", "Senior", "Lead", "Associate", "Trainee", "Principal", ""]
