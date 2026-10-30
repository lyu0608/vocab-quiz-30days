-- ========================================================
-- v6.2.1 by Claude：Supabase RLS policy 修正紀錄
-- 已於 2026-10-30 在 Supabase SQL Editor 執行完成，此檔僅供紀錄 / 重建資料庫時使用
-- ========================================================

-- v6.2.1 by Claude：原 policy 為 USING true 且對象為 public，
-- 未登入者即可讀取全體學員 email 與每日上線紀錄；改為僅管理者可讀取全部
drop policy "Admin can read all daily activity" on public.user_daily_activity;
create policy "Admin can read all daily activity" on public.user_daily_activity
  for select to authenticated
  using ((auth.jwt() ->> 'email') = 'lyu0608@gmail.com');

-- v6.2.1 by Claude：原本只有「本人可更新自己的進度」，管理者在後台修改他人啟動日期會被 RLS 擋下 (0 筆更新、無錯誤)；
-- 新增管理者可更新任一學員進度的權限
create policy "Admin can update all progress" on public.user_progress
  for update to authenticated
  using ((auth.jwt() ->> 'email') = 'lyu0608@gmail.com')
  with check ((auth.jwt() ->> 'email') = 'lyu0608@gmail.com');
