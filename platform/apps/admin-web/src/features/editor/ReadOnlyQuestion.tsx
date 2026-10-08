import { LockKeyhole } from 'lucide-react';
import type { EditableQuestion } from './model/definition';

export function ReadOnlyQuestion({ question }: { question: EditableQuestion }) {
  return (
    <div className="editor-readonly" role="note">
      <LockKeyhole aria-hidden="true" />
      <div>
        <h2>{question.text}</h2>
        <p>
          题型 {question.type} 当前为只读。编码 {question.code} 及全部扩展配置会原样保留。
        </p>
      </div>
    </div>
  );
}
