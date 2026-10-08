export type BasicQuestionType = 'X' | 'L' | 'M' | 'S' | 'T';
export type JsonObject = Record<string, unknown>;

export interface EditableChoice {
  source: JsonObject;
  code: string;
  text: string;
}

export interface EditableQuestion {
  source: JsonObject;
  uuid: string;
  code: string;
  type: string;
  text: string;
  editable: boolean;
  mandatory?: boolean;
  other?: boolean;
  answers?: EditableChoice[];
}

export interface EditableGroup {
  source: JsonObject;
  uuid: string;
  title: string;
  description?: string;
  questions: EditableQuestion[];
}

export interface EditableSurveyDefinition {
  source: JsonObject;
  definitionVersion: number;
  title: string;
  description?: string;
  language: string;
  theme?: string;
  settings?: Record<string, string>;
  groups: EditableGroup[];
}

const basicQuestionTypes = new Set<string>(['X', 'L', 'M', 'S', 'T']);

export function parseDefinition(value: unknown): EditableSurveyDefinition {
  const source = objectValue(value, '问卷定义');
  const groups = arrayValue(source.groups, '问卷题组').map(parseGroup);
  return {
    source: cloneJson(source),
    definitionVersion: numberValue(source.definitionVersion, '定义版本'),
    title: stringValue(source.title, '问卷标题'),
    description: optionalString(source.description),
    language: stringValue(source.language, '问卷语言'),
    theme: optionalString(source.theme),
    settings: stringMap(source.settings),
    groups,
  };
}

export function serializeDefinition(definition: EditableSurveyDefinition): unknown {
  const serialized = cloneJson(definition.source);
  serialized.definitionVersion = definition.definitionVersion;
  serialized.title = definition.title;
  setOptional(serialized, 'description', definition.description);
  serialized.language = definition.language;
  setOptional(serialized, 'theme', definition.theme);
  if (definition.settings !== undefined) serialized.settings = cloneJson(definition.settings);
  serialized.groups = definition.groups.map(serializeGroup);
  return serialized;
}

export function createBasicQuestion(
  type: BasicQuestionType,
  uuid: string,
  code: string,
): EditableQuestion {
  const labels: Record<BasicQuestionType, string> = {
    X: '说明文字',
    L: '单选题',
    M: '多选题',
    S: '短文本题',
    T: '长文本题',
  };
  const source: JsonObject = { uuid, code, type, text: labels[type] };
  let answers: EditableChoice[] | undefined;
  if (type !== 'X') source.mandatory = false;
  if (type === 'L' || type === 'M') {
    source.other = false;
    const answerSource = { code: 'A1', text: '选项 1' };
    source.answers = [answerSource];
    answers = [{ source: cloneJson(answerSource), ...answerSource }];
  }
  return {
    source: cloneJson(source),
    uuid,
    code,
    type,
    text: labels[type],
    editable: true,
    mandatory: type === 'X' ? undefined : false,
    other: type === 'L' || type === 'M' ? false : undefined,
    answers,
  };
}

function parseGroup(value: unknown): EditableGroup {
  const source = objectValue(value, '题组');
  return {
    source: cloneJson(source),
    uuid: stringValue(source.uuid, '题组 UUID'),
    title: stringValue(source.title, '题组标题'),
    description: optionalString(source.description),
    questions: arrayValue(source.questions, '题目').map(parseQuestion),
  };
}

function parseQuestion(value: unknown): EditableQuestion {
  const source = objectValue(value, '题目');
  const type = stringValue(source.type, '题型');
  const editable = basicQuestionTypes.has(type);
  const answers = editable && (type === 'L' || type === 'M') && Array.isArray(source.answers)
    ? source.answers.map(parseChoice)
    : undefined;
  return {
    source: cloneJson(source),
    uuid: stringValue(source.uuid, '题目 UUID'),
    code: stringValue(source.code, '题目编码'),
    type,
    text: stringValue(source.text, '题目文本'),
    editable,
    mandatory: editable && typeof source.mandatory === 'boolean' ? source.mandatory : undefined,
    other: editable && typeof source.other === 'boolean' ? source.other : undefined,
    answers,
  };
}

function parseChoice(value: unknown): EditableChoice {
  const source = objectValue(value, '选项');
  return {
    source: cloneJson(source),
    code: typeof source.code === 'string' ? source.code : '',
    text: typeof source.text === 'string' ? source.text : '',
  };
}

function serializeGroup(group: EditableGroup): JsonObject {
  const serialized = cloneJson(group.source);
  serialized.uuid = group.uuid;
  serialized.title = group.title;
  setOptional(serialized, 'description', group.description);
  serialized.questions = group.questions.map(serializeQuestion);
  return serialized;
}

function serializeQuestion(question: EditableQuestion): JsonObject {
  const serialized = cloneJson(question.source);
  if (!question.editable) return serialized;
  serialized.uuid = question.uuid;
  serialized.code = question.code;
  serialized.text = question.text;
  setOptional(serialized, 'mandatory', question.mandatory);
  setOptional(serialized, 'other', question.other);
  if (question.answers !== undefined) {
    serialized.answers = question.answers.map((answer) => ({
      ...cloneJson(answer.source),
      code: answer.code,
      text: answer.text,
    }));
  }
  return serialized;
}

function objectValue(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label}必须是对象`);
  }
  return value as JsonObject;
}

function arrayValue(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label}必须是数组`);
  return value;
}

function stringValue(value: unknown, label: string): string {
  if (typeof value !== 'string') throw new Error(`${label}必须是文本`);
  return value;
}

function numberValue(value: unknown, label: string): number {
  if (typeof value !== 'number') throw new Error(`${label}必须是数字`);
  return value;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function stringMap(value: unknown): Record<string, string> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const entries = Object.entries(value);
  if (entries.some(([, item]) => typeof item !== 'string')) return undefined;
  return Object.fromEntries(entries) as Record<string, string>;
}

function setOptional(target: JsonObject, key: string, value: unknown) {
  if (value !== undefined) target[key] = value;
}

function cloneJson<T>(value: T): T {
  return structuredClone(value);
}
