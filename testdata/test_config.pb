account:{
  name: "checking"
  value: 5432.10
  rate: 0.001
  timespec: "0 0 1 * *"
  sweep_out {
    sweep_account: "savings"
    amount: 8000
  }
}
account:{
  name: "savings"
  value: 54321.0
  rate: 0.001
  timespec: "0 0 1 * *"
  sweep_in {
    sweep_account: "checking"
    amount: 60000
  }
}
account:{
  name: "house"
  value: 675000
  rate: 0.031
  timespec: "0 0 1 * *"
}
account:{
  name: "mortgage"
  value: -350000
  rate: 0.045
  timespec: "0 0 1 * *"
  start_date: "2013-10-01"
  loan_months: 360
}
cashflow:{
  name: "his salary"
  timespec: "0 0 1,15 * *"
  amount: 3000
  account: "checking"
}
cashflow:{
  name: "her salary"
  timespec: "0 0 1,15 * *"
  amount: 3200
  account: "checking"
}
cashflow:{
  name: "credit card"
  timespec: "0 0 8 * *"
  amount: -2500
  account: "checking"
  stddev: 950
}
cashflow:{
  name: "property tax"
  timespec: "0 0 1 1,6 *"
  amount: -3100
  account: "checking"
}
cashflow:{
  name: "mortgage payment"
  timespec: "0 0 1 * *"
  amount: -2500
  start_date: "2013-10-01"
  account: "checking"
}
inflation: 0.035
property_tax: 0.0123
start_date: "2013-01-01"
end_date: "2044-01-01"
