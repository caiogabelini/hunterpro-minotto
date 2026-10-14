import { describe, expect, it } from "vitest";

import { kanbanPodeEstarIncompleto } from "./kanbanLimite";

describe("kanbanPodeEstarIncompleto", () => {
  it("avisa quando recebeu exatamente o limite (o backend cortou)", () => {
    expect(kanbanPodeEstarIncompleto(1000, 1000)).toBe(true);
  });

  it("não avisa abaixo do limite (todos os leads vieram)", () => {
    expect(kanbanPodeEstarIncompleto(120, 1000)).toBe(false);
    expect(kanbanPodeEstarIncompleto(999, 1000)).toBe(false);
  });

  it("não avisa com banco vazio", () => {
    expect(kanbanPodeEstarIncompleto(0, 1000)).toBe(false);
  });

  it("avisa se por algum motivo vier mais que o limite", () => {
    expect(kanbanPodeEstarIncompleto(1001, 1000)).toBe(true);
  });

  it.each([0, -1, NaN, Infinity])("limite inválido (%p) nunca avisa", (limite) => {
    expect(kanbanPodeEstarIncompleto(0, limite)).toBe(false);
    expect(kanbanPodeEstarIncompleto(50, limite)).toBe(false);
  });
});
