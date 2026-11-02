import type { EditableSurveyDefinition } from './model/definition';

interface PropertiesPanelProps {
  definition: EditableSurveyDefinition;
  disabled: boolean;
  onChange(update: Pick<EditableSurveyDefinition, 'description' | 'language' | 'title'>): void;
}

export function PropertiesPanel({ definition, disabled, onChange }: PropertiesPanelProps) {
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
