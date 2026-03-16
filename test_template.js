const detailedList = [{qty: 1, name: "Atta", unit_price: 45, total: 45}];
const html = `
    <tbody>
        ${detailedList.length > 0 
            ? detailedList.map(item => `<tr><td>${item.qty}</td></tr>`).join('') 
            : 'default'}
    </tbody>
`;
console.log(html);
