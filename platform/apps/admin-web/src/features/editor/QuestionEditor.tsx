import type { EditableQuestion } from './model/definition';
import type { ApiClient } from '../../shared/api/http';
import { AdvancedQuestionOptions } from './AdvancedQuestionOptions';
import { ReadOnlyQuestion } from './ReadOnlyQuestion';

interface QuestionEditorProps {
  question: EditableQuestion;
  api: ApiClient;
  tenantId: string;
  disabled: boolean;
  onChange(update: Partial<Pick<EditableQuestion, 'answers' | 'code' | 'mandatory' | 'other' | 'text' | 'themeOptions'>>): void;
}

export function QuestionEditor({ question, api, tenantId, disabled, onChange }: QuestionEditorProps) {
  if (!question.editable) return <ReadOnlyQuestion question={question} />;
  if (question.source.theme === 'mjy-carousel' && (!Array.isArray(question.themeOptions?.slides)
    || question.themeOptions.slides.some((slide) => !slide || typeof slide !== 'object' || Array.isArray(slide)))) {
    return <div><p role="alert">轮播图配置格式无法可视化编辑，原始配置将保留。</p><ReadOnlyQuestion question={question} /></div>;
  }
  if (question.source.theme === 'mjy-cascading-select' && (!Array.isArray(question.themeOptions?.levels)
    || question.themeOptions.levels.some((level) => typeof level !== 'string'))) {
    return <div><p role="alert">多级下拉配置格式无法可视化编辑，原始配置将保留。</p><ReadOnlyQuestion question={question} /></div>;
  }
  const labels: Record<string, string> = { X: '说明文字', L: '单选题', M: '多选题', S: '短文本题', T: '长文本题',
    'mjy-collapsible': '折叠栏目', 'mjy-cascading-select': '多级下拉', 'mjy-carousel': '轮播图' };

  return (
    <div className="question-editor">
      <div className="editor-section-heading">
        <div>
          <p>{labels[String(question.source.theme)] ?? labels[question.type] ?? question.type}</p>
          <h2>编辑题目</h2>
        </div>
      </div>
      <label>
        题目编码
        <input
          aria-label="题目编码"
          value={question.code}
          disabled={disabled}
          onChange={(event) => onChange({ code: event.target.value })}
        />
      </label>
      <label>
        题目文本
        <textarea
          aria-label="题目文本"
          rows={5}
          value={question.text}
          disabled={disabled}
          onChange={(event) => onChange({ text: event.target.value })}
        />
      </label>
      {question.type === 'X' && question.source.theme === 'mjy-collapsible' && question.themeOptions ? (
        <fieldset disabled={disabled}>
          <legend>折叠栏目</legend>
          <label>栏目标题<input value={typeof question.themeOptions.summary === 'string' ? question.themeOptions.summary : ''}
            onChange={(event) => onChange({ themeOptions: { ...question.themeOptions, summary: event.target.value } })} /></label>
          <label className="editor-checkbox-row"><input type="checkbox" checked={question.themeOptions.collapsed !== false}
            onChange={(event) => onChange({ themeOptions: { ...question.themeOptions, collapsed: event.target.checked } })} />默认折叠</label>
        </fieldset>
      ) : null}
      {question.themeOptions && (question.source.theme === 'mjy-cascading-select' || question.source.theme === 'mjy-carousel') ? (
        <AdvancedQuestionOptions api={api} tenantId={tenantId} question={question} disabled={disabled}
          onChange={(themeOptions) => onChange({ themeOptions })} />
      ) : null}
      {question.type !== 'X' ? (
        <label className="editor-checkbox-row">
          <input
            type="checkbox"
            checked={question.mandatory ?? false}
            disabled={disabled}
            onChange={(event) => onChange({ mandatory: event.target.checked })}
          />
          必答题
        </label>
      ) : null}
      {question.answers ? (
        <fieldset disabled={disabled}>
          <legend>选项</legend>
          {question.answers.map((answer, index) => (
            <div className="editor-choice-row" key={`${index}-${answer.code}`}>
              <input
                aria-label={`选项 ${index + 1} 编码`}
                value={answer.code}
                onChange={(event) =>
                  onChange({
                    answers: question.answers?.map((item, itemIndex) =>
                      itemIndex === index ? { ...item, code: event.target.value } : item,
                    ),
                    ...(question.source.theme === 'mjy-carousel' && question.themeOptions && Array.isArray(question.themeOptions.slides)
                      ? { themeOptions: { ...question.themeOptions, slides: question.themeOptions.slides.map((slide: unknown) =>
                        slide && typeof slide === 'object' && !Array.isArray(slide) && (slide as Record<string, unknown>).code === answer.code
                          ? { ...slide, code: event.target.value } : slide) } } : {}),
                  })
                }
              />
              <input
                aria-label={`选项 ${index + 1} 文本`}
                value={answer.text}
                onChange={(event) =>
                  onChange({
                    answers: question.answers?.map((item, itemIndex) =>
                      itemIndex === index ? { ...item, text: event.target.value } : item,
                    ),
                  })
                }
              />
              <button type="button" aria-label={`删除选项 ${index + 1}`} disabled={(question.answers?.length ?? 0) <= 1}
                onClick={() => onChange({
                  answers: question.answers?.filter((_, i) => i !== index),
                  ...(question.source.theme === 'mjy-carousel' && question.themeOptions && Array.isArray(question.themeOptions.slides)
                    ? { themeOptions: { ...question.themeOptions, slides: question.themeOptions.slides.filter((slide: unknown) =>
                      !(slide && typeof slide === 'object' && !Array.isArray(slide) && (slide as Record<string, unknown>).code === answer.code)) } } : {}),
                })}>删除</button>
            </div>
          ))}
          <button type="button" onClick={() => {
            const answers = question.answers ?? [];
            const codes = new Set(answers.map((item) => item.code.toUpperCase()));
            let index = answers.length + 1;
            while (codes.has(`A${index}`)) index++;
            const code = `A${index}`;
            const choice = { code, text: `选项 ${answers.length + 1}` };
            onChange({ answers: [...answers, { ...choice, source: choice }],
              ...(question.source.theme === 'mjy-carousel' && question.themeOptions && Array.isArray(question.themeOptions.slides)
                ? { themeOptions: { ...question.themeOptions, slides: [...question.themeOptions.slides, { code, assetId: '', alt: '' }] } } : {}) });
          }}>添加选项</button>
          <label className="editor-checkbox-row">
            <input
              type="checkbox"
              checked={question.other ?? false}
              onChange={(event) => onChange({ other: event.target.checked })}
            />
            允许填写其他答案
          </label>
        </fieldset>
      ) : null}
    </div>
  );
}
