module.exports = function selecionar(inputs) {
  if (String(inputs.todas) === 'true') return Array.from({length: 50}, (_, i) => i + 1);
  const tokens = String(inputs.contas || '').trim().split(/[,;\s]+/);
  if (tokens.some(t => !/^\d{1,2}$/.test(t) || +t < 1 || +t > 50)) {
    throw new Error('Informe números de 1 a 50 separados por vírgulas ou marque Todas as contas.');
  }
  return [...new Set(tokens.map(Number))];
};
