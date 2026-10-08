import type { EditableQuestion, EditableSurveyDefinition } from '../editor/model/definition';

export function DraftRenderer({ definition }: { definition: EditableSurveyDefinition }) {
  return (
    <article className="draft-renderer" aria-label="只读草稿内容">
      <header>
        <h2>{definition.title}</h2>
        {definition.description ? <p>{definition.description}</p> : null}
      </header>
      {definition.groups.map((group) => (
        <section className="draft-group" key={group.uuid}>
          <h3>{group.title}</h3>
          {group.description ? <p>{group.description}</p> : null}
          <ol>
            {group.questions.map((question) => (
              <li key={question.uuid}>
                <QuestionPreview question={question} />
              </li>
            ))}
          </ol>
        </section>
      ))}
    </article>
  );
}

function QuestionPreview({ question }: { question: EditableQuestion }) {
  if (!question.editable) {
    return (
      <div className="draft-question draft-question-unsupported" role="note">
        <QuestionHeading question={question} />
        <p>题型 {question.type} 暂不支持交互预览，草稿数据保持不变。</p>
      </div>
    );
  }

  if (question.type === 'X') {
    return <div className="draft-question draft-note"><p>{question.text}</p></div>;
  }

  return (
    <fieldset className="draft-question" disabled>
      <legend>
        <span>{question.code}</span>
        {question.text}
        {question.mandatory ? <strong>必答</strong> : null}
      </legend>
      {question.type === 'L'
        ? question.answers?.map((answer) => (
            <label key={answer.code}>
              <input type="radio" name={question.uuid} disabled />
              {answer.text}
            </label>
          ))
        : null}
      {question.type === 'M'
        ? question.answers?.map((answer) => (
            <label key={answer.code}>
              <input type="checkbox" disabled />
              {answer.text}
            </label>
          ))
        : null}
      {question.type === 'S' ? <input aria-label={question.text} type="text" readOnly /> : null}
      {question.type === 'T' ? <textarea aria-label={question.text} rows={4} readOnly /> : null}
    </fieldset>
  );
}

function QuestionHeading({ question }: { question: EditableQuestion }) {
  return (
    <h4>
      <span>{question.code}</span>
      {question.text}
    </h4>
  );
}
