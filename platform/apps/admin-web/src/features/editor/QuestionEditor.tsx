import type { EditableQuestion } from './model/definition';
import type { ApiClient } from '../../shared/api/http';
import { AdvancedQuestionOptions } from './AdvancedQuestionOptions';
import { ReadOnlyQuestion } from './ReadOnlyQuestion';

interface QuestionEditorProps {
  question: EditableQuestion;
  supportsLogic: boolean;
  conditionSources: EditableQuestion[];
  api: ApiClient;
  tenantId: string;
  disabled: boolean;
  onChange(update: Partial<Pick<EditableQuestion, 'answers' | 'code' | 'condition' | 'mandatory' | 'other' | 'text' | 'themeOptions' | 'slideIds'>>): void;
  onSlideAssetChange(questionUuid: string, slideId: string, assetId: string): void;
}

export function QuestionEditor({ question, supportsLogic, conditionSources, api, tenantId, disabled, onChange, onSlideAssetChange }: QuestionEditorProps) {
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
  const simpleCondition = /^(?:q\("([0-9a-fA-F-]{36})"\)|([A-Za-z][A-Za-z0-9]*)) == "([A-Za-z0-9]{1,5})"$/.exec(question.condition ?? '');
  const selectedSource = simpleCondition ? conditionSources.find((source) =>
    (simpleCondition[1] ? source.uuid === simpleCondition[1] : source.code === simpleCondition[2])
    && source.answers?.some((answer) => answer.code === simpleCondition[3])) : undefined;
  const complexCondition = Boolean(question.condition && !selectedSource);

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
      {supportsLogic ? (
        <fieldset disabled={disabled}>
          <legend>显示条件</legend>
          {complexCondition ? (
            <p role="status">现有复杂显示条件会保持原样。若需改成单选条件，请先清除。</p>
          ) : null}
          <label>前置题目
            <select aria-label="前置题目" disabled={complexCondition} value={selectedSource?.code ?? ''} onChange={(event) => {
              const source = conditionSources.find((item) => item.code === event.target.value);
              const answer = source?.answers?.find((item) => /^[A-Za-z0-9]{1,5}$/.test(item.code));
              onChange({ condition: source && answer ? `q(${JSON.stringify(source.uuid)}) == ${JSON.stringify(answer.code)}` : '' });
            }}>
              <option value="">始终显示</option>
              {conditionSources.map((source) => <option key={source.uuid} value={source.code}>{source.text}</option>)}
            </select>
          </label>
          {selectedSource ? <label>触发选项
            <select aria-label="触发选项" value={simpleCondition?.[3] ?? ''} onChange={(event) =>
              onChange({ condition: `q(${JSON.stringify(selectedSource.uuid)}) == ${JSON.stringify(event.target.value)}` })}>
              {selectedSource.answers?.filter((answer) => /^[A-Za-z0-9]{1,5}$/.test(answer.code))
                .map((answer) => <option key={answer.code} value={answer.code}>{answer.text}</option>)}
            </select>
          </label> : null}
          {complexCondition ? <button type="button" onClick={() => onChange({ condition: '' })}>清除显示条件</button> : null}
        </fieldset>
      ) : null}
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
          onChange={(themeOptions, slideIds) => onChange({ themeOptions, slideIds })} onSlideAssetChange={onSlideAssetChange} />
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
                      !(slide && typeof slide === 'object' && !Array.isArray(slide) && (slide as Record<string, unknown>).code === answer.code)) },
                      slideIds: question.slideIds?.filter((_, i) => {
                        const slide = (question.themeOptions!.slides as unknown[])[i];
                        return !(slide && typeof slide === 'object' && !Array.isArray(slide) && (slide as Record<string, unknown>).code === answer.code);
                      }) } : {}),
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
                ? { themeOptions: { ...question.themeOptions, slides: [...question.themeOptions.slides, { code, assetId: '', alt: '' }] },
                  slideIds: [...(question.slideIds ?? []), crypto.randomUUID()] } : {}) });
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
