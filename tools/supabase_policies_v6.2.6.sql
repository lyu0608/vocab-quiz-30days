-- ========================================================
-- v6.2.6 by Claude：學員寫入資料時 email 只能是自己登入的 email
-- 已於 2026-10-30 在 Supabase SQL Editor 執行完成，此檔僅供紀錄 / 重建資料庫時使用
-- 執行前已確認現有資料 (user_progress 3 筆、user_daily_activity 6 筆) 的 email 皆與帳號一致
-- ========================================================

-- v6.2.6 by Claude：原本只檢查 user_id 是本人，email 欄位可任意填寫 (可冒名其他學員或寫入惡意 HTML)；
-- 加上 email 必須等於登入憑證 (JWT) 中的 email
-- 註：這條 policy 名稱寫 update，實際是 INSERT 規則 (upsert 新增時套用)
alter policy "Users can update their own progress" on public.user_progress
  with check (auth.uid() = user_id and email = (auth.jwt() ->> 'email'));

-- v6.2.6 by Claude：UPDATE 規則同樣限制 (upsert 更新既有資料時套用)
alter policy "Users can modify their own progress" on public.user_progress
  with check (auth.uid() = user_id and email = (auth.jwt() ->> 'email'));

-- v6.2.6 by Claude：每日上線紀錄同樣限制
alter policy "Users can manage their own daily activity" on public.user_daily_activity
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id and email = (auth.jwt() ->> 'email'));

-- 管理者修改啟動日期走 "Admin can update all progress" 規則 (v6.2.1)，不受此限制影響
