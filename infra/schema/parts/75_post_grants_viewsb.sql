-- Extra grants for the business views B (analytics, ext_*, notifications_community, gig_marketplace) that the RoleMatrix-generated
-- 70_grants.sql does not give to the role named in the view's `security` text (views_over_table only follows base-table access).
-- Roles named in the security text: service role -> app_service, compliance -> compliance_svc, BI -> analytics_reader,
-- operators/curators/support leads -> app_staff_admin + app_staff_super, TPO/employer for the drive funnel, client -> app_employer.
GRANT SELECT ON TABLE vw_ad_campaign_pacing, vw_sponsored_job_serving, vw_active_account_restriction, vw_notification_delivery_latest, vw_active_user_signal TO app_service;
-- @@
GRANT SELECT ON TABLE vw_eeo_aggregate_cell_count TO compliance_svc;
-- @@
GRANT SELECT ON TABLE vw_search_index_lag, vw_zero_result_top_queries, vw_raw_title_review_queue, vw_experiment_readout, vw_support_ticket_sla_breach TO app_staff_admin, app_staff_super;
-- @@
GRANT SELECT ON TABLE vw_drive_funnel TO app_tpo, app_employer, app_staff_admin, app_staff_super;
-- @@
GRANT SELECT ON TABLE vw_client_vendor_submission, vw_ad_audience_rule_public TO app_employer;
-- @@
GRANT SELECT ON TABLE vw_unified_demand_daily, vw_dim_organisation_current, vw_dim_job_requirement_current, vw_kpi_catalogue, vw_pipeline_latency,
  vw_etl_freshness, vw_gig_vs_job_fill, vw_recommendation_ctr_hourly, vw_experiment_readout, mv_funnel_last_90_days, mv_kpi_summary_monthly,
  mv_recruiter_leaderboard_30d, mv_mrr_trend, mv_wallet_cover_risk, vw_dim_candidate_current, vw_dim_user_current TO analytics_reader;
-- @@
GRANT SELECT ON TABLE vw_certificate_public_verification TO app_service;
