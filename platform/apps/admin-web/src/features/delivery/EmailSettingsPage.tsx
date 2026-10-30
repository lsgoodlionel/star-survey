import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useAuth } from '../auth/AuthProvider';
import { deleteEmailSetting, emailSettingQueryKey, getEmailSetting, saveEmailSetting, sendTestEmail, verifyEmailSetting, type SaveEmailSettingInput } from '../../shared/api/delivery';

const initial: SaveEmailSettingInput = { provider: 'netease-163', authMode: 'app-password', fromAddress: '', username: '', credential: '' };

export function EmailSettingsPage() {
  const { api, session } = useAuth(); const qc = useQueryClient(); const [form, setForm] = useState(initial); const [notice, setNotice] = useState<string | null>(null);
  const [testRecipient, setTestRecipient] = useState('');
  const [testSubject, setTestSubject] = useState('问卷平台邮件投递测试');
  const [testBody, setTestBody] = useState('这是一封由问卷平台邮件服务设置页发送的测试邮件。');
  const key = emailSettingQueryKey(session!.me.tenantId);
  const setting = useQuery({ queryKey: key, queryFn: ({ signal }) => getEmailSetting(api, signal) });
  const refresh = () => qc.invalidateQueries({ queryKey: key });
  const save = useMutation({ mutationFn: () => saveEmailSetting(api, form), onSuccess: () => { setForm((v) => ({ ...v, credential: '' })); setNotice('邮件服务配置已加密保存。授权码不会再次显示。'); refresh(); }, onError: () => setNotice('保存失败，请检查权限、邮箱与授权信息。') });
  const verify = useMutation({ mutationFn: () => verifyEmailSetting(api, form), onSuccess: (v) => setNotice(v.message), onError: () => setNotice('验证失败；未发送邮件。') });
  const remove = useMutation({ mutationFn: () => deleteEmailSetting(api), onSuccess: () => { setNotice('邮件服务配置已清除。'); setForm(initial); refresh(); }, onError: () => setNotice('清除失败。') });
  const testSend = useMutation({
    mutationFn: () => sendTestEmail(api, { recipient: testRecipient.trim(), subject: testSubject.trim(), body: testBody.trim() }),
    onSuccess: (result) => setNotice(result.message),
    onError: () => setNotice('测试邮件发送失败，请检查收件邮箱和已保存的邮件配置。'),
  });
  const update = <K extends keyof SaveEmailSettingInput>(key: K, value: SaveEmailSettingInput[K]) => setForm((v) => ({ ...v, [key]: value }));
  return <main className="settings-page"><h1>邮件服务设置</h1><p>授权码只会在保存或验证时提交，不会回显、写入浏览器存储或显示在投递记录中。</p>
    {setting.data?.configured ? <p className="settings-status">已配置：{setting.data.fromAddress}（{setting.data.provider}）</p> : <p className="settings-status">尚未配置邮件服务</p>}
    <form onSubmit={(e) => { e.preventDefault(); setNotice(null); save.mutate(); }}>
      <label>服务商<select value={form.provider} onChange={(e) => { const provider = e.target.value as SaveEmailSettingInput['provider']; update('provider', provider); update('authMode', provider === 'outlook-com' ? 'oauth2' : 'app-password'); }}><option value="netease-163">网易 163</option><option value="gmail">Gmail</option><option value="outlook-com">Hotmail / Outlook.com</option></select></label>
      <label>认证方式<select value={form.authMode} disabled={form.provider === 'outlook-com'} onChange={(e) => update('authMode', e.target.value as SaveEmailSettingInput['authMode'])}><option value="app-password">客户端授权码</option><option value="oauth2">OAuth2 Access Token</option></select></label>
      <label>发件邮箱<input type="email" required value={form.fromAddress} onChange={(e) => update('fromAddress', e.target.value)} /></label>
      <label>登录邮箱<input type="email" required value={form.username} onChange={(e) => update('username', e.target.value)} /></label>
      <label>授权码 / Access Token<input type="password" required autoComplete="new-password" value={form.credential} onChange={(e) => update('credential', e.target.value)} /></label>
      <div className="settings-actions"><button type="button" disabled={verify.isPending || !form.credential} onClick={() => { setNotice(null); verify.mutate(); }}>仅验证登录</button><button type="submit" disabled={save.isPending}>加密保存</button>{setting.data?.configured ? <button type="button" disabled={remove.isPending} onClick={() => remove.mutate()}>清除配置</button> : null}</div>
    </form>
    <section className="settings-test-email" aria-labelledby="settings-test-email-title">
      <h2 id="settings-test-email-title">发送测试邮件</h2>
      <p>使用已加密保存的邮件配置真实发送一封测试邮件。SMTP 接受不等于最终进入收件箱，请同时检查垃圾邮件目录。</p>
      <label>测试收件邮箱<input aria-label="测试收件邮箱" type="email" required value={testRecipient} onChange={(event) => setTestRecipient(event.target.value)} /></label>
      <label>测试邮件主题<input aria-label="测试邮件主题" maxLength={200} value={testSubject} onChange={(event) => setTestSubject(event.target.value)} /></label>
      <label>测试邮件正文<textarea aria-label="测试邮件正文" maxLength={10000} value={testBody} onChange={(event) => setTestBody(event.target.value)} /></label>
      <button type="button" disabled={!setting.data?.configured || !testRecipient.trim() || !testSubject.trim() || !testBody.trim() || testSend.isPending}
        onClick={() => { setNotice(null); testSend.mutate(); }}>
        {testSend.isPending ? '正在发送测试邮件' : '发送测试邮件'}
      </button>
      {!setting.data?.configured ? <p>请先完成“加密保存”，再发送测试邮件。</p> : null}
    </section>
    {notice ? <p role="status">{notice}</p> : null}</main>;
}
