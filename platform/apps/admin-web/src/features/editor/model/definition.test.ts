import { describe, expect, test } from 'vitest';
import gatewayFixture from '../../../test/fixtures/publish-gateway.json';
import {
  createBasicQuestion,
  parseDefinition,
  serializeDefinition,
} from './definition';
import {
  moveGroup,
  moveQuestion,
  updateQuestion,
  validateDefinition,
} from './operations';

const singleUuid = '33333333-0001-4111-8111-000000000001';
const textUuid = '33333333-0002-4111-8111-000000000002';
const noteUuid = '33333333-0003-4111-8111-000000000003';
const complexUuid = '33333333-0004-4111-8111-000000000004';
const firstGroupUuid = '22222222-0001-4111-8111-000000000001';
const secondGroupUuid = '22222222-0002-4111-8111-000000000002';

function sourceWithExtensions() {
  const source = structuredClone(gatewayFixture) as Record<string, unknown>;
  source.workflow = {
    reviewers: ['author', { team: 'quality', rules: [1, null, { strict: true }] }],
  };
  const groups = source.groups as Array<Record<string, unknown>>;
  groups[0].extension = { display: ['compact', { color: '#146c5a' }] };
  const questions = groups[0].questions as Array<Record<string, unknown>>;
  questions[0].futureQuestionField = {
    nested: [{ keep: true }, ['verbatim', 7]],
  };
  const answers = questions[0].answers as Array<Record<string, unknown>>;
  answers[0].futureAnswerField = { score: [1, 2, { weight: 3 }] };
  return source;
}

describe('lossless survey definition model', () => {
  test('roundTripsEveryFieldTheEditorDoesNotOwn', () => {
    const source = sourceWithExtensions();

    const serialized = serializeDefinition(parseDefinition(source));

    expect(serialized).toEqual(source);
    expect(serialized).not.toBe(source);
  });

  test('editsOnlyTheSelectedBasicQuestion', () => {
    const source = sourceWithExtensions();
    const definition = parseDefinition(source);

    const edited = updateQuestion(definition, singleUuid, {
      code: 'QCHANGED',
      text: 'Changed prompt',
      type: 'S',
    } as never);
    const serialized = serializeDefinition(edited) as typeof gatewayFixture;
    const expected = structuredClone(source) as typeof gatewayFixture;
    expected.groups[0].questions[0].code = 'QCHANGED';
    expected.groups[0].questions[0].text = 'Changed prompt';

    expect(serialized).toEqual(expected);
    expect(serialized.groups[0].questions[0].type).toBe('L');
    expect(edited.groups[1].questions[0].editable).toBe(false);
    expect(edited.groups[1].questions[1].editable).toBe(false);
  });

  test('keepsQuestionAndGroupUuidsStableWhileReordering', () => {
    const definition = parseDefinition(sourceWithExtensions());
    const beforeQuestionUuids = definition.groups.flatMap((group) =>
      group.questions.map((question) => question.uuid),
    );
    const beforeGroupUuids = definition.groups.map((group) => group.uuid);

    const withMovedQuestion = moveQuestion(definition, noteUuid, secondGroupUuid, 0);
    const reordered = moveGroup(withMovedQuestion, secondGroupUuid, 0);

    expect(reordered.groups.map((group) => group.uuid)).toEqual([
      secondGroupUuid,
      firstGroupUuid,
    ]);
    expect(reordered.groups[0].questions.map((question) => question.uuid)).toEqual([
      noteUuid,
      complexUuid,
      '33333333-0005-4111-8111-000000000005',
    ]);
    expect(new Set(reordered.groups.map((group) => group.uuid))).toEqual(
      new Set(beforeGroupUuids),
    );
    expect(new Set(reordered.groups.flatMap((group) => group.questions.map((question) => question.uuid)))).toEqual(
      new Set(beforeQuestionUuids),
    );
  });

  test('rejectsDuplicateCodesAndBlankOptionsBeforeSave', () => {
    let definition = parseDefinition(sourceWithExtensions());
    definition = updateQuestion(definition, textUuid, { code: 'QSINGLE' });
    definition = updateQuestion(definition, singleUuid, {
      answers: definition.groups[0].questions[0].answers?.map((answer, index) =>
        index === 1 ? { ...answer, text: '   ' } : answer,
      ),
    });

    expect(validateDefinition(definition)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'duplicate-question-code', questionUuid: textUuid }),
        expect.objectContaining({ code: 'blank-choice', questionUuid: singleUuid }),
      ]),
    );
  });

  test('createsStableDefaultsForXLSMAndTQuestionTypes', () => {
    const types = ['X', 'L', 'S', 'M', 'T'] as const;

    for (const [index, type] of types.entries()) {
      const uuid = `55555555-000${index + 1}-4555-8555-00000000000${index + 1}`;
      const first = createBasicQuestion(type, uuid, `Q${index + 1}`);
      const second = createBasicQuestion(type, uuid, `Q${index + 1}`);
      expect(first).toEqual(second);
      expect(first).toMatchObject({ uuid, code: `Q${index + 1}`, type, editable: true });
      if (type === 'L' || type === 'M') {
        expect(first.answers).toEqual([
          expect.objectContaining({ code: 'A1', text: '选项 1' }),
        ]);
      } else {
        expect(first.answers).toBeUndefined();
      }
    }
  });
});
