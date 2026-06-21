from supabase_client import supabase_admin

def check_db_stats():
    try:
        # Get count of leads
        leads_res = supabase_admin.table('leads').select('id', count='exact').limit(0).execute()
        leads_count = leads_res.count or 0
        
        # Get count of call attempts
        calls_res = supabase_admin.table('call_attempts').select('id', count='exact').limit(0).execute()
        calls_count = calls_res.count or 0
        
        # Get count of profiles
        profiles_res = supabase_admin.table('profiles').select('id', count='exact').limit(0).execute()
        profiles_count = profiles_res.count or 0
        
        print(f"=== Supabase Database Row Counts ===")
        print(f"Leads:          {leads_count:,} rows")
        print(f"Call Attempts:  {calls_count:,} rows")
        print(f"Profiles:       {profiles_count:,} rows")
        
        # Estimated storage calculation:
        # A typical lead row with names, emails, campaigns, etc., is about 0.5 KB to 1 KB.
        # A typical call log is about 0.3 KB.
        est_leads_kb = leads_count * 0.8
        est_calls_kb = calls_count * 0.3
        est_profiles_kb = profiles_count * 0.5
        total_est_mb = (est_leads_kb + est_calls_kb + est_profiles_kb) / 1024
        
        print(f"\n=== Estimated Database Size ===")
        print(f"Estimated Table Data: {total_est_mb:.2f} MB")
        print(f"Supabase Free Tier Limit: 500.00 MB")
        print(f"Percent of Free Tier Used: {(total_est_mb / 500.00) * 100:.2f}%")
        
    except Exception as e:
        print("Error checking stats:", e)

if __name__ == '__main__':
    check_db_stats()
