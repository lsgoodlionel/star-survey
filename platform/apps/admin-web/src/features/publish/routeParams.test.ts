import { describe, expect, test } from 'vitest';
import { parsePositiveIntegerParam, parseSurveyIdParam } from './routeParams';

describe('publish route parameters', () => {
  test.each([
    ['11111111-1111-4111-8111-111111111111', '11111111-1111-4111-8111-111111111111'],
    [undefined, null],
    ['', null],
    ['11111111-1111-4111-8111-11111111111', null],
    ['11111111-1111-4111-8111-11111111111Z', null],
    ['AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA', null],
    ['{11111111-1111-4111-8111-111111111111}', null],
  ])('parses strict canonical survey UUID %s', (value, expected) => {
    expect(parseSurveyIdParam(value)).toBe(expected);
  });

  test.each([
    ['1', 1],
    ['42', 42],
    [undefined, null],
    ['', null],
    ['0', null],
    ['-1', null],
    ['01', null],
    ['1.0', null],
    ['1e3', null],
    ['9007199254740992', null],
  ])('parses canonical positive integer %s', (value, expected) => {
    expect(parsePositiveIntegerParam(value)).toBe(expected);
  });
});
