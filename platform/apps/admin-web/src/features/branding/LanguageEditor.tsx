import { useState } from 'react';

type Row = Record<string, unknown>;
const object = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value)
  ? value as Row : {};
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(object) : [];
const string = (value: unknown) => typeof value === 'string' ? value : '';

interface LanguageEditorProps {
  definition: unknown;
  languages: string[];
  translations: Record<string, unknown>;
  disabled: boolean;
  onChange(languages: string[], translations: Record<string, unknown>): void;
}

export function LanguageEditor({ definition, languages, translations, disabled, onChange }: LanguageEditorProps) {
  const source = object(definition);
  const base = string(source.language) || 'zh-Hans';
  const groups = rows(source.groups);
  const [newLanguage, setNewLanguage] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);

  function addLanguage() {
    const code = newLanguage.trim();
    if (!/^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(code) || code === base || languages.includes(code)) {
      setError('请输入有效且未使用的附加语言代码'); return;
    }
    onChange([...languages, code], translations);
    setNewLanguage(''); setError(null);
  }
  function update(language: string, section: 'title' | 'description' | 'groups' | 'questions' | 'subquestions',
    field: string, value: string, uuid?: string) {
    const current = { ...object(translations[language]) };
    if (uuid) {
      const entries = { ...object(current[section]) };
      const entry = { ...object(entries[uuid]) };
      if (value) entry[field] = value; else delete entry[field];
      if (Object.keys(entry).length) entries[uuid] = entry; else delete entries[uuid];
      if (Object.keys(entries).length) current[section] = entries; else delete current[section];
    } else if (value) current[section] = value;
    else delete current[section];
    const next = { ...translations };
    if (Object.keys(current).length) next[language] = current; else delete next[language];
    onChange(languages, next);
  }
  function updateAnswer(language: string, question: string, code: string, scale: number, value: string) {
    const current = { ...object(translations[language]) };
    const answers = rows(current.answers).filter((item) =>
      !(item.question === question && item.code === code && (item.scale ?? 0) === scale));
    if (value) answers.push({ question, code, scale, text: value });
    if (answers.length) current.answers = answers; else delete current.answers;
    const next = { ...translations };
    if (Object.keys(current).length) next[language] = current; else delete next[language];
    onChange(languages, next);
  }
  return <section className="language-editor" aria-label="多语言译文">
    <h2>多语言</h2>
    <p>基础语言：{base}。留空的译文使用基础语言内容。</p>
    {!disabled ? <div className="language-add">
      <label>新增语言代码 <input value={newLanguage} onChange={(event) => setNewLanguage(event.target.value)} /></label>
      <button type="button" onClick={addLanguage}>添加语言</button>
      {error ? <p role="alert">{error}</p> : null}
    </div> : null}
    {languages.map((language) => {
      const translated = object(translations[language]);
      return <section key={language} aria-label={`${language} 译文`}>
        <h3>{language}</h3>
        {!disabled ? pendingRemoval === language ? <div>
          <span>移除 {language} 及其译文？</span>
          <button type="button" onClick={() => { const next = { ...translations }; delete next[language];
            onChange(languages.filter((item) => item !== language), next); setPendingRemoval(null); }}>确认移除</button>
          <button type="button" onClick={() => setPendingRemoval(null)}>取消</button>
        </div> : <button type="button" onClick={() => setPendingRemoval(language)}>移除 {language}</button> : null}
        <label>{language} 问卷标题 <input disabled={disabled} value={string(translated.title)}
          onChange={(event) => update(language, 'title', 'title', event.target.value)} /></label>
        <label>{language} 问卷说明 <textarea disabled={disabled} value={string(translated.description)}
          onChange={(event) => update(language, 'description', 'description', event.target.value)} /></label>
        {groups.map((group) => <div key={string(group.uuid)}>
          <label>{language} 题组 {string(group.title)} 标题 <input disabled={disabled}
            value={string(object(object(translated.groups)[string(group.uuid)]).title)}
            onChange={(event) => update(language, 'groups', 'title', event.target.value, string(group.uuid))} /></label>
          <label>{language} 题组 {string(group.title)} 说明 <input disabled={disabled}
            value={string(object(object(translated.groups)[string(group.uuid)]).description)}
            onChange={(event) => update(language, 'groups', 'description', event.target.value, string(group.uuid))} /></label>
          {rows(group.questions).map((question) => <div key={string(question.uuid)}>
            <label>{language} 题目 {string(question.code)} 文本 <input disabled={disabled}
              value={string(object(object(translated.questions)[string(question.uuid)]).text)}
              onChange={(event) => update(language, 'questions', 'text', event.target.value, string(question.uuid))} /></label>
            <label>{language} 题目 {string(question.code)} 帮助 <input disabled={disabled}
              value={string(object(object(translated.questions)[string(question.uuid)]).help)}
              onChange={(event) => update(language, 'questions', 'help', event.target.value, string(question.uuid))} /></label>
            {rows(question.subquestions).map((subquestion) => <label key={string(subquestion.uuid)}>
              {language} 子题 {string(subquestion.code)} 文本 <input disabled={disabled}
                value={string(object(object(translated.subquestions)[string(subquestion.uuid)]).text)}
                onChange={(event) => update(language, 'subquestions', 'text', event.target.value,
                  string(subquestion.uuid))} />
            </label>)}
            {rows(question.answers).map((answer) => {
              const scale = typeof answer.scale === 'number' ? answer.scale : 0;
              const previous = rows(translated.answers).find((item) => item.question === question.uuid
                && item.code === answer.code && (item.scale ?? 0) === scale);
              return <label key={`${string(answer.code)}:${scale}`}>{language} 选项 {string(question.code)} / {string(answer.code)}
                <input disabled={disabled} value={string(previous?.text)} onChange={(event) =>
                  updateAnswer(language, string(question.uuid), string(answer.code), scale, event.target.value)} />
              </label>;
            })}
          </div>)}
        </div>)}
      </section>;
    })}
  </section>;
}
