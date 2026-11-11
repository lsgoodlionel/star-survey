import type { EditableSurveyDefinition } from './model/definition';
import { readTimeWindow, type TimeWindow } from './model/timeWindow';
import { readIpDenyList } from './model/ipDenyList';

interface PropertiesPanelProps {
  definition: EditableSurveyDefinition;
  disabled: boolean;
  onChange(update: Pick<EditableSurveyDefinition, 'description' | 'language' | 'title'>): void;
  onTimeWindowChange(window: TimeWindow | null): void;
  onIpDenyListChange(denyIps: string[]): void;
}

export function PropertiesPanel({ definition, disabled, onChange, onTimeWindowChange, onIpDenyListChange }: PropertiesPanelProps) {
  const timeWindow = readTimeWindow(definition);
  const window = timeWindow.window;
  const windowDisabled = disabled || !timeWindow.editable;
  const ipDenyList = readIpDenyList(definition);
  return (
    <div className="editor-properties">
      <h2>问卷属性</h2>
      <label>
        标题
        <input
          value={definition.title}
          disabled={disabled}
          onChange={(event) =>
            onChange({
              title: event.target.value,
              description: definition.description,
              language: definition.language,
            })
          }
        />
      </label>
      <label>
        描述
        <textarea
          rows={4}
          value={definition.description ?? ''}
          disabled={disabled}
          onChange={(event) =>
            onChange({
              title: definition.title,
              description: event.target.value,
              language: definition.language,
            })
          }
        />
      </label>
      <fieldset>
        <legend>开放时间窗</legend>
        {!timeWindow.editable ? <p role="alert">现有访问策略格式无法在此编辑；原设置会保留。</p> : null}
        <label>启用开放时间窗 <input type="checkbox" checked={window !== null} disabled={windowDisabled}
          onChange={(event) => onTimeWindowChange(event.target.checked ? { timezone: 'Asia/Shanghai' } : null)} /></label>
        {window ? <>
          <p>至少填写开放或截止时间；按所填时区解释本地时间，截止时刻不再接受作答。</p>
          <label>开放时间 <input type="datetime-local" value={window.opensAt ?? ''} disabled={windowDisabled}
            onChange={(event) => onTimeWindowChange({ ...window, opensAt: event.target.value })} /></label>
          <label>截止时间 <input type="datetime-local" value={window.closesAt ?? ''} disabled={windowDisabled}
            onChange={(event) => onTimeWindowChange({ ...window, closesAt: event.target.value })} /></label>
          <label>时区 <input type="text" value={window.timezone} disabled={windowDisabled} placeholder="Asia/Shanghai"
            onChange={(event) => onTimeWindowChange({ ...window, timezone: event.target.value })} /></label>
          <p>时区使用 IANA 名称；夏令时不存在或重叠的时刻由服务端拒绝。</p>
        </> : null}
      </fieldset>
      <fieldset>
        <legend>网络访问</legend>
        {!ipDenyList.editable ? <p role="alert">现有网络策略格式无法在此编辑；原设置会保留。</p> : null}
        <label>拒绝访问的 IP/CIDR
          <textarea rows={4} value={ipDenyList.denyIps.join('\n')} disabled={disabled || !ipDenyList.editable}
            onChange={(event) => onIpDenyListChange(event.target.value === '' ? [] : event.target.value.split(/\r?\n/))} />
        </label>
        <p>每行一个 IPv4、IPv6 地址或 CIDR；不能留空行。若清空后只剩无效网络设置，请恢复拒绝项或先移除其它设置。不合法地址由发布网关拒绝，保存草稿不代表规则已生效。</p>
      </fieldset>
      <div>
        <p>语言：<span>{languageLabel(definition.language)}</span></p>
        <details>
          <summary>技术信息</summary>
          <label>语言代码 <input
          value={definition.language}
          disabled={disabled}
          onChange={(event) =>
            onChange({
              title: definition.title,
              description: definition.description,
              language: event.target.value,
            })
          }
          /></label>
        </details>
      </div>
    </div>
  );
}

function languageLabel(language: string) {
  const labels: Record<string, string> = { 'zh-Hans': '简体中文', 'zh-Hant': '繁体中文', en: '英语' };
  return labels[language] ?? language;
}
