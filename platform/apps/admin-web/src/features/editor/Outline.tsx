import { ChevronDown, ChevronUp } from 'lucide-react';
import type { EditableSurveyDefinition } from './model/definition';

interface OutlineProps {
  definition: EditableSurveyDefinition;
  selectedQuestionUuid: string | null;
  disabled: boolean;
  onMoveQuestion(questionUuid: string, groupUuid: string, targetIndex: number): void;
  onSelect(questionUuid: string): void;
}

export function Outline({
  definition,
  selectedQuestionUuid,
  disabled,
  onMoveQuestion,
  onSelect,
}: OutlineProps) {
  return (
    <div className="editor-outline">
      <h2>问卷大纲</h2>
      {definition.groups.map((group) => (
        <section className="editor-outline-group" key={group.uuid}>
          <h3>{group.title}</h3>
          <ul>
            {group.questions.map((question, index) => (
              <li key={question.uuid}>
                <button
                  className="editor-outline-question"
                  type="button"
                  aria-label={`${question.code} ${question.text}`}
                  aria-current={question.uuid === selectedQuestionUuid ? 'true' : undefined}
                  onClick={() => onSelect(question.uuid)}
                >
                  <span>{question.code}</span>
                  <small>{question.text}</small>
                </button>
                <div className="editor-reorder-actions" aria-label={`${question.code} 排序`}>
                  <button
                    type="button"
                    title="上移题目"
                    aria-label={`上移 ${question.code}`}
                    disabled={disabled || index === 0}
                    onClick={() => onMoveQuestion(question.uuid, group.uuid, index - 1)}
                  >
                    <ChevronUp aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    title="下移题目"
                    aria-label={`下移 ${question.code}`}
                    disabled={disabled || index === group.questions.length - 1}
                    onClick={() => onMoveQuestion(question.uuid, group.uuid, index + 1)}
                  >
                    <ChevronDown aria-hidden="true" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
