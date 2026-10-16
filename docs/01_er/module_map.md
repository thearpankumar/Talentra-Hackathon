# Module map

High-level dependency map. An arrow `A --> B (n)` means A has n foreign keys pointing into tables owned by B.

```mermaid
flowchart LR
  master_data["Master data<br/>70 tables"]
  profile["Profile<br/>90 tables"]
  jobs_applications["Jobs and Applications<br/>99 tables"]
  administration["Administration<br/>88 tables"]
  organisation_account["Organisation and account<br/>65 tables"]
  gig_marketplace["Gig marketplace<br/>65 tables"]
  licences_devices["Licences, seats, devices and sessions<br/>34 tables"]
  sourcing_consultant["Sourcing and consultant requests<br/>42 tables"]
  subscriptions_payments["Subscriptions, wallet, payments and download history<br/>74 tables"]
  notifications_community["Notifications, preferences and community<br/>93 tables"]
  analytics["Analytics layer and reporting marts<br/>80 tables"]
  tier3_reference["Tier 3 reference domains (optional)<br/>24 tables"]
  ext_network["Extension: Network, connections and graph<br/>88 tables"]
  ext_matching["Extension: Recommendations and matching signals<br/>78 tables"]
  ext_ads["Extension: Ads and sponsored placements<br/>79 tables"]
  ext_learning["Extension: Learning and assessments marketplace<br/>112 tables"]
  ext_talent_ops["Extension: Talent operations (onboarding, background checks, CRM, trust and safety)<br/>125 tables"]
  administration -->|1| licences_devices
  administration -->|7| master_data
  administration -->|1| notifications_community
  administration -->|6| organisation_account
  administration -->|6| profile
  analytics -->|17| master_data
  ext_ads -->|9| administration
  ext_ads -->|1| gig_marketplace
  ext_ads -->|4| jobs_applications
  ext_ads -->|2| licences_devices
  ext_ads -->|34| master_data
  ext_ads -->|6| organisation_account
  ext_ads -->|1| profile
  ext_ads -->|6| subscriptions_payments
  ext_learning -->|3| administration
  ext_learning -->|2| jobs_applications
  ext_learning -->|63| master_data
  ext_learning -->|8| organisation_account
  ext_learning -->|52| profile
  ext_learning -->|2| subscriptions_payments
  ext_learning -->|1| tier3_reference
  ext_matching -->|15| administration
  ext_matching -->|15| jobs_applications
  ext_matching -->|25| master_data
  ext_matching -->|4| organisation_account
  ext_matching -->|16| profile
  ext_matching -->|1| tier3_reference
  ext_network -->|64| administration
  ext_network -->|2| jobs_applications
  ext_network -->|26| master_data
  ext_network -->|8| notifications_community
  ext_network -->|8| organisation_account
  ext_network -->|9| profile
  ext_network -->|1| subscriptions_payments
  ext_talent_ops -->|51| administration
  ext_talent_ops -->|22| jobs_applications
  ext_talent_ops -->|40| master_data
  ext_talent_ops -->|3| notifications_community
  ext_talent_ops -->|30| organisation_account
  ext_talent_ops -->|15| profile
  gig_marketplace -->|10| administration
  gig_marketplace -->|31| master_data
  gig_marketplace -->|2| organisation_account
  jobs_applications -->|16| administration
  jobs_applications -->|43| master_data
  jobs_applications -->|13| organisation_account
  jobs_applications -->|9| profile
  jobs_applications -->|1| subscriptions_payments
  licences_devices -->|19| administration
  licences_devices -->|1| master_data
  licences_devices -->|4| organisation_account
  master_data -->|1| organisation_account
  notifications_community -->|39| administration
  notifications_community -->|5| jobs_applications
  notifications_community -->|1| licences_devices
  notifications_community -->|42| master_data
  notifications_community -->|9| organisation_account
  notifications_community -->|3| profile
  organisation_account -->|17| administration
  organisation_account -->|31| master_data
  profile -->|14| administration
  profile -->|2| gig_marketplace
  profile -->|4| jobs_applications
  profile -->|1| licences_devices
  profile -->|63| master_data
  profile -->|7| organisation_account
  profile -->|3| subscriptions_payments
  sourcing_consultant -->|14| administration
  sourcing_consultant -->|6| jobs_applications
  sourcing_consultant -->|17| master_data
  sourcing_consultant -->|10| organisation_account
  sourcing_consultant -->|8| profile
  sourcing_consultant -->|2| subscriptions_payments
  subscriptions_payments -->|11| administration
  subscriptions_payments -->|1| jobs_applications
  subscriptions_payments -->|4| licences_devices
  subscriptions_payments -->|31| master_data
  subscriptions_payments -->|11| organisation_account
  subscriptions_payments -->|2| profile
  subscriptions_payments -->|2| sourcing_consultant
  tier3_reference -->|2| administration
  tier3_reference -->|1| analytics
  tier3_reference -->|18| master_data
  tier3_reference -->|3| organisation_account
```

| Module | Owner | Tables | Lookups | Detailed ER |
|---|---|---|---|---|
| Master data | Team | 70 | 42 | [master_data.md](master_data.md) |
| Profile | Team | 90 | 38 | [profile.md](profile.md) |
| Jobs and Applications | Team | 99 | 42 | [jobs_applications.md](jobs_applications.md) |
| Administration | Member A | 88 | 42 | [administration.md](administration.md) |
| Organisation and account | Member B | 65 | 27 | [organisation_account.md](organisation_account.md) |
| Gig marketplace | Member C | 65 | 31 | [gig_marketplace.md](gig_marketplace.md) |
| Licences, seats, devices and sessions | Member A | 34 | 15 | [licences_devices.md](licences_devices.md) |
| Sourcing and consultant requests | Member B | 42 | 15 | [sourcing_consultant.md](sourcing_consultant.md) |
| Subscriptions, wallet, payments and download history | Member C | 74 | 35 | [subscriptions_payments.md](subscriptions_payments.md) |
| Notifications, preferences and community | Team | 93 | 28 | [notifications_community.md](notifications_community.md) |
| Analytics layer and reporting marts | Team | 80 | 5 | [analytics.md](analytics.md) |
| Tier 3 reference domains (optional) | Team | 24 | 10 | [tier3_reference.md](tier3_reference.md) |
| Extension: Network, connections and graph | Team | 88 | 36 | [ext_network.md](ext_network.md) |
| Extension: Recommendations and matching signals | Team | 78 | 32 | [ext_matching.md](ext_matching.md) |
| Extension: Ads and sponsored placements | Team | 79 | 36 | [ext_ads.md](ext_ads.md) |
| Extension: Learning and assessments marketplace | Team | 112 | 43 | [ext_learning.md](ext_learning.md) |
| Extension: Talent operations (onboarding, background checks, CRM, trust and safety) | Team | 125 | 61 | [ext_talent_ops.md](ext_talent_ops.md) |
