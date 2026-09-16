function main(workbook: ExcelScript.Workbook) {
  const cuadrante = workbook.getWorksheet("cuadrante");
  const conversion = workbook.getWorksheet("conversión comité");

  // Última fila de trabajadores (columna A desde la fila 3)
  const nombres = cuadrante.getRange("A3:A2000").getValues();
  let ultimaFila = 2;
  for (let i = 0; i < nombres.length; i++) {
    if (nombres[i][0] === "") break;
    ultimaFila = 3 + i;
  }

  // Última fila de la tabla de conversión (columna A desde la fila 3)
  const ids = conversion.getRange("A3:A2000").getValues();
  let ultimaFilaConversion = 2;
  for (let i = 0; i < ids.length; i++) {
    if (ids[i][0] === "") break;
    ultimaFilaConversion = 3 + i;
  }

  // Mapa: id de turno -> { valor, celda J } (el valor se lee una sola vez aquí,
  // no en el bucle del calendario, para no llamar a getValue() miles de veces)
  const plantillas = new Map<string, { valor: string | number | boolean; celda: ExcelScript.Range }>();
  for (let fila = 3; fila <= ultimaFilaConversion; fila++) {
    const id = conversion.getRange(`A${fila}`).getValue();
    if (id === "") continue;
    const celda = conversion.getRange(`J${fila}`);
    plantillas.set(String(id).trim(), { valor: celda.getValue(), celda });
  }

  // Recrear la hoja "pdf"
  const pdfAnterior = workbook.getWorksheets().find(hoja => hoja.getName() === "pdf");
  if (pdfAnterior) pdfAnterior.delete();
  const pdf = workbook.addWorksheet("pdf");
  pdf.setPosition(cuadrante.getPosition() + 1);

  // Clonado base tal cual: cabecera, columnas A-C y calendario sin cambios
  const origen = cuadrante.getRange(`A1:ND${ultimaFila}`);
  const destino = pdf.getRange(`A1:ND${ultimaFila}`);
  destino.copyFrom(origen, ExcelScript.RangeCopyType.all);

  // Sustituir valores del calendario (D en adelante) en un solo paso
  const calendarioOrigen = cuadrante.getRange(`D3:ND${ultimaFila}`);
  const valores = calendarioOrigen.getValues();
  const nuevosValores = valores.map(fila =>
    fila.map(celda => {
      const plantilla = plantillas.get(String(celda).trim());
      return plantilla ? plantilla.valor : celda;
    })
  );
  const calendarioDestino = pdf.getRange(`D3:ND${ultimaFila}`);
  calendarioDestino.setValues(nuevosValores);

  // Formato por lotes: una regla de formato condicional por turno,
  // no una operación por celda (evita el error de payload por exceso de operaciones)
  plantillas.forEach(plantilla => {
    const valor = plantilla.valor;
    const formula = typeof valor === "number" ? `=${valor}` : `="${String(valor).replace(/"/g, '""')}"`;

    const regla = calendarioDestino.addConditionalFormat(ExcelScript.ConditionalFormatType.cellValue).getCellValue();
    regla.setRule({ formula1: formula, operator: ExcelScript.ConditionalCellValueOperator.equalTo });

    const formatoOrigen = plantilla.celda.getFormat();
    const formatoDestino = regla.getFormat();
    formatoDestino.getFill().setColor(formatoOrigen.getFill().getColor());
    formatoDestino.getFont().setColor(formatoOrigen.getFont().getColor());
    formatoDestino.getFont().setBold(formatoOrigen.getFont().getBold());
    formatoDestino.getFont().setItalic(formatoOrigen.getFont().getItalic());
  });

  // Preparar la página para exportar a PDF
  pdf.getPageLayout().setPrintArea(`A1:ND${ultimaFila}`);
  pdf.getPageLayout().setOrientation(ExcelScript.PageOrientation.landscape);
  pdf.getPageLayout().setZoom({ horizontalFitToPages: 1 });

  // Dejar "pdf" como única hoja visible, para que la conversión a PDF no incluya las demás
  pdf.activate();
  for (const hoja of workbook.getWorksheets()) {
    hoja.setVisibility(hoja.getName() === "pdf" ? ExcelScript.SheetVisibility.visible : ExcelScript.SheetVisibility.hidden);
  }
}
