import type { ImportPreview } from '../../shared/api/imports';

interface ImportPreviewTableProps {
  preview: ImportPreview;
  selected: ReadonlySet<number>;
  onSelectionChange(index: number, checked: boolean): void;
}

export function ImportPreviewTable({
  preview,
  selected,
  onSelectionChange,
}: ImportPreviewTableProps) {
  return (
    <div className="import-preview-results">
      <div className="import-table-wrap">
        <table className="import-preview-table">
          <thead>
            <tr>
              <th scope="col">选择</th>
              <th scope="col">原文位置</th>
              <th scope="col">题目</th>
              <th scope="col">题型</th>
              <th scope="col">识别结果</th>
            </tr>
          </thead>
          <tbody>
            {preview.questions.map((question) => (
              <tr key={question.index}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${question.code} ${question.text}`}
                    checked={selected.has(question.index)}
                    disabled={!question.importable}
                    onChange={(event) => onSelectionChange(question.index, event.target.checked)}
                  />
                </td>
                <td>第 {question.line} 行</td>
                <td>
                  <strong>{question.code}</strong>
                  <span>{question.text}</span>
                  {question.options.length ? (
                    <ul>
                      {question.options.map((option) => (
                        <li key={option.code}>{option.code} {option.text}</li>
                      ))}
                    </ul>
                  ) : null}
                </td>
                <td>
                  {question.typeName}
                  {question.typeInferred ? <small>自动推断</small> : null}
                </td>
                <td>
                  {question.importable ? (
                    <span className="import-ok">可以导入</span>
                  ) : (
                    <ProblemList problems={question.problems} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {preview.problems.length ? (
        <section className="import-bad-lines" aria-labelledby="bad-lines-title">
          <h2 id="bad-lines-title">未识别的原文</h2>
          <ProblemList problems={preview.problems} />
        </section>
      ) : null}
    </div>
  );
}

function ProblemList({ problems }: { problems: ImportPreview['problems'] }) {
  return (
    <ul className="import-problems">
      {problems.map((problem, index) => (
        <li key={`${problem.line}:${problem.code}:${index}`}>
          <strong>第 {problem.line} 行</strong>
          <span>{problem.message}</span>
        </li>
      ))}
    </ul>
  );
}
