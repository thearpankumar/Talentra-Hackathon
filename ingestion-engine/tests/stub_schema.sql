-- Stub Talentra schema used to develop/test the ingestion engine.
-- The real schema lands in ../infra/schema/ddl/*.sql; the engine introspects the live DB.
CREATE TABLE IF NOT EXISTS country (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code STRING(2) NOT NULL UNIQUE,
  name STRING(100) NOT NULL,
  phone_country_code STRING(5) NOT NULL,
  currency_code STRING(3) NOT NULL,
  is_active BOOL NOT NULL DEFAULT true,
  sort_order INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS language (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code STRING(8) NOT NULL UNIQUE,
  name STRING(100) NOT NULL,
  is_active BOOL NOT NULL DEFAULT true,
  sort_order INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS city (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  country_id UUID NOT NULL REFERENCES country(id),
  name STRING(150) NOT NULL,
  latitude DECIMAL(9,6),
  longitude DECIMAL(9,6),
  is_active BOOL NOT NULL DEFAULT true,
  UNIQUE (country_id, name)
);
CREATE TABLE IF NOT EXISTS industry (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code STRING(40) NOT NULL UNIQUE,
  name STRING(120) NOT NULL,
  is_active BOOL NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS job_category (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code STRING(40) NOT NULL UNIQUE,
  name STRING(120) NOT NULL,
  is_blue_collar BOOL NOT NULL DEFAULT false,
  is_active BOOL NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS skill (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code STRING(60) NOT NULL UNIQUE,
  name STRING(120) NOT NULL,
  job_category_id UUID REFERENCES job_category(id),
  is_active BOOL NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email STRING(254) NOT NULL UNIQUE CHECK (email = lower(email)),
  phone_e164 STRING(16) UNIQUE CHECK (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  phone_country_id UUID REFERENCES country(id),
  full_name STRING(150) NOT NULL,
  preferred_language_id UUID REFERENCES language(id),
  role STRING(20) NOT NULL CHECK (role IN ('candidate','employer_admin','recruiter','admin')),
  status STRING(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','pending','closed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by STRING(60) NOT NULL DEFAULT 'system',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by STRING(60) NOT NULL DEFAULT 'system',
  deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS candidate_profile (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL UNIQUE REFERENCES users(id),
  headline STRING(200),
  country_id UUID REFERENCES country(id),
  current_city_id UUID REFERENCES city(id),
  primary_skill_id UUID REFERENCES skill(id),
  years_experience DECIMAL(4,1) CHECK (years_experience >= 0 AND years_experience <= 60),
  education_level STRING(20) CHECK (education_level IN ('none','secondary','diploma','bachelor','master','doctorate')),
  gender STRING(20) CHECK (gender IN ('female','male','other','undisclosed')),
  date_of_birth DATE,
  notice_period_days INT CHECK (notice_period_days >= 0),
  expected_salary_min DECIMAL(12,2),
  expected_salary_max DECIMAL(12,2),
  salary_currency STRING(3),
  salary_disclosure STRING(20) NOT NULL DEFAULT 'disclosed' CHECK (salary_disclosure IN ('disclosed','unknown','not_applicable')),
  resume_text STRING,
  resume_language_code STRING(8),
  is_open_to_work BOOL NOT NULL DEFAULT true,
  latitude DECIMAL(9,6),
  longitude DECIMAL(9,6),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by STRING(60) NOT NULL DEFAULT 'system',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by STRING(60) NOT NULL DEFAULT 'system',
  deleted_at TIMESTAMPTZ,
  CHECK (expected_salary_min <= expected_salary_max)
);
CREATE INDEX IF NOT EXISTS idx_candidate_profile_country_id ON candidate_profile (country_id);

CREATE TABLE IF NOT EXISTS candidate_skill (
  candidate_profile_id UUID NOT NULL REFERENCES candidate_profile(id),
  skill_id UUID NOT NULL REFERENCES skill(id),
  proficiency INT NOT NULL CHECK (proficiency BETWEEN 1 AND 5),
  years_used DECIMAL(4,1) CHECK (years_used >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (candidate_profile_id, skill_id)
);

CREATE TABLE IF NOT EXISTS employer (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name STRING(200) NOT NULL,
  industry_id UUID REFERENCES industry(id),
  country_id UUID NOT NULL REFERENCES country(id),
  size_band STRING(20) NOT NULL CHECK (size_band IN ('1-10','11-50','51-200','201-1000','1001-5000','5000+')),
  registration_number STRING(60) UNIQUE,
  website STRING(200),
  contact_email STRING(254) CHECK (contact_email = lower(contact_email)),
  contact_phone_e164 STRING(16),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by STRING(60) NOT NULL DEFAULT 'system',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by STRING(60) NOT NULL DEFAULT 'system',
  deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS job_requirement (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employer_id UUID NOT NULL REFERENCES employer(id),
  title STRING(200) NOT NULL,
  job_category_id UUID NOT NULL REFERENCES job_category(id),
  country_id UUID NOT NULL REFERENCES country(id),
  city_id UUID REFERENCES city(id),
  employment_type STRING(20) NOT NULL CHECK (employment_type IN ('full_time','part_time','contract','gig','internship')),
  min_experience_years DECIMAL(4,1) CHECK (min_experience_years >= 0),
  max_experience_years DECIMAL(4,1),
  salary_min DECIMAL(12,2),
  salary_max DECIMAL(12,2),
  salary_currency STRING(3),
  salary_disclosure STRING(20) NOT NULL DEFAULT 'disclosed' CHECK (salary_disclosure IN ('disclosed','unknown','not_applicable')),
  openings INT NOT NULL DEFAULT 1 CHECK (openings > 0),
  status STRING(20) NOT NULL DEFAULT 'open' CHECK (status IN ('draft','open','paused','closed','filled')),
  description STRING,
  description_language_code STRING(8),
  posted_at TIMESTAMPTZ NOT NULL,
  closes_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by STRING(60) NOT NULL DEFAULT 'system',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by STRING(60) NOT NULL DEFAULT 'system',
  deleted_at TIMESTAMPTZ,
  CHECK (salary_min <= salary_max),
  CHECK (min_experience_years <= max_experience_years),
  CHECK (closes_at >= posted_at)
);
CREATE INDEX IF NOT EXISTS idx_job_requirement_employer_id ON job_requirement (employer_id);

CREATE TABLE IF NOT EXISTS application (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_requirement_id UUID NOT NULL REFERENCES job_requirement(id),
  candidate_profile_id UUID NOT NULL REFERENCES candidate_profile(id),
  status STRING(20) NOT NULL CHECK (status IN ('applied','shortlisted','interview','offer','hired','rejected','withdrawn')),
  match_score DECIMAL(5,2) CHECK (match_score >= 0 AND match_score <= 100),
  applied_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by STRING(60) NOT NULL DEFAULT 'system',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by STRING(60) NOT NULL DEFAULT 'system',
  UNIQUE (job_requirement_id, candidate_profile_id)
);
CREATE INDEX IF NOT EXISTS idx_application_candidate_profile_id ON application (candidate_profile_id);
