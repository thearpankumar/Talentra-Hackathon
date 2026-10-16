-- Reference/master data for the stub schema (the engine only references these rows).
INSERT INTO country (code,name,phone_country_code,currency_code,sort_order) VALUES
('IN','India','+91','INR',1),('NG','Nigeria','+234','NGN',2),('AE','United Arab Emirates','+971','AED',3),
('SA','Saudi Arabia','+966','SAR',4),('PH','Philippines','+63','PHP',5),('US','United States','+1','USD',6),
('GB','United Kingdom','+44','GBP',7),('DE','Germany','+49','EUR',8),('FR','France','+33','EUR',9),
('BR','Brazil','+55','BRL',10),('MX','Mexico','+52','MXN',11),('CN','China','+86','CNY',12),
('JP','Japan','+81','JPY',13),('BD','Bangladesh','+880','BDT',14),('PK','Pakistan','+92','PKR',15),
('KE','Kenya','+254','KES',16),('EG','Egypt','+20','EGP',17),('RU','Russia','+7','RUB',18)
ON CONFLICT (code) DO NOTHING;
INSERT INTO language (code,name,sort_order) VALUES
('en','English',1),('hi','Hindi',2),('ta','Tamil',3),('ar','Arabic',4),('yo','Yoruba',5),('fil','Filipino',6),
('zh','Chinese',7),('ja','Japanese',8),('es','Spanish',9),('pt','Portuguese',10),('fr','French',11),('de','German',12),
('bn','Bengali',13),('ur','Urdu',14),('sw','Swahili',15),('ru','Russian',16)
ON CONFLICT (code) DO NOTHING;
INSERT INTO industry (code,name) VALUES ('it','Information Technology'),('bpo','BPO & Customer Support'),('construction','Construction'),
('hospitality','Hospitality'),('logistics','Logistics'),('healthcare','Healthcare'),('retail','Retail'),('manufacturing','Manufacturing'),
('finance','Banking & Finance'),('education','Education'),('oil_gas','Oil & Gas'),('design','Design & Creative')
ON CONFLICT (code) DO NOTHING;
INSERT INTO job_category (code,name,is_blue_collar) VALUES ('software','Software Engineering',false),('data','Data & Analytics',false),
('design','Design',false),('sales','Sales & Marketing',false),('electrician','Electrician',true),('driver','Driver',true),
('welder','Welder',true),('security','Security Guard',true),('cook','Cook / Chef',true),('nurse','Nursing',false),
('warehouse','Warehouse',true),('cleaner','Cleaning & Housekeeping',true),('accounting','Accounting',false),('teaching','Teaching',false)
ON CONFLICT (code) DO NOTHING;
INSERT INTO skill (code,name,job_category_id)
SELECT s.code, s.name, (SELECT id FROM job_category WHERE code=s.cat) FROM (VALUES
('java','Java','software'),('python','Python','software'),('javascript','JavaScript','software'),('react','React','software'),
('sql','SQL','data'),('spark','Apache Spark','data'),('figma','Figma','design'),('photoshop','Photoshop','design'),
('wiring','Electrical Wiring','electrician'),('arc_welding','Arc Welding','welder'),('forklift','Forklift Operation','warehouse'),
('heavy_vehicle','Heavy Vehicle Driving','driver'),('cooking','Commercial Cooking','cook'),('patient_care','Patient Care','nurse'),
('excel','Excel','accounting'),('tally','Tally','accounting'),('crm','CRM Tools','sales'),('first_aid','First Aid','security'),
('golang','Go','software'),('aws','AWS','software')) AS s(code,name,cat)
ON CONFLICT (code) DO NOTHING;
INSERT INTO city (country_id,name,latitude,longitude)
SELECT (SELECT id FROM country WHERE code=c.cc), c.name, c.lat, c.lon FROM (VALUES
('IN','Bengaluru',12.9716,77.5946),('IN','Mumbai',19.0760,72.8777),('IN','Delhi',28.6139,77.2090),('IN','Hyderabad',17.3850,78.4867),
('IN','Chennai',13.0827,80.2707),('IN','Pune',18.5204,73.8567),('NG','Lagos',6.5244,3.3792),('NG','Abuja',9.0765,7.3986),
('AE','Dubai',25.2048,55.2708),('AE','Abu Dhabi',24.4539,54.3773),('SA','Riyadh',24.7136,46.6753),('SA','Jeddah',21.4858,39.1925),
('PH','Manila',14.5995,120.9842),('PH','Cebu',10.3157,123.8854),('US','New York',40.7128,-74.0060),('US','Austin',30.2672,-97.7431),
('GB','London',51.5074,-0.1278),('DE','Berlin',52.52,13.405),('FR','Paris',48.8566,2.3522),('BR','Sao Paulo',-23.5505,-46.6333),
('MX','Mexico City',19.4326,-99.1332),('CN','Shanghai',31.2304,121.4737),('JP','Tokyo',35.6762,139.6503),('BD','Dhaka',23.8103,90.4125),
('PK','Karachi',24.8607,67.0011),('KE','Nairobi',-1.2921,36.8219),('EG','Cairo',30.0444,31.2357),('RU','Moscow',55.7558,37.6173)
) AS c(cc,name,lat,lon) ON CONFLICT (country_id,name) DO NOTHING;
