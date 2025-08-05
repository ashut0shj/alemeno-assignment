from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db import transaction
from django.urls import path
from datetime import date
from decimal import Decimal
import pandas as pd

from .models import (
    Customer, Loan, CustomerRegistrationSerializer, LoanEligibilitySerializer, 
    LoanEligibilityResponseSerializer, LoanCreateSerializer,
    LoanCreateResponseSerializer, LoanDetailSerializer, CustomerLoanListSerializer
)


@api_view(['GET'])
def api_root(request):
    return Response({
        'message': 'Credit Approval System API',
        'endpoints': {
            'register': '/api/register/',
            'check_eligibility': '/api/check-eligibility/',
            'create_loan': '/api/create-loan/',
            'view_loan': '/api/view-loan/{loan_id}/',
            'view_customer_loans': '/api/view-loans/{customer_id}/',
            'ingest_data': '/api/ingest-data/'
        },
        'methods': {
            'register': 'GET, POST',
            'check_eligibility': 'POST',
            'create_loan': 'POST', 
            'view_loan': 'GET',
            'view_customer_loans': 'GET',
            'ingest_data': 'POST'
        }
    })


@api_view(['GET', 'POST'])
def register_customer(request):
    if request.method == 'GET':
        return Response({
            'message': 'Customer Registration API',
            'method': 'POST',
            'fields': {
                'first_name': 'string',
                'last_name': 'string', 
                'age': 'integer',
                'monthly_salary': 'decimal',
                'phone_number': 'string'
            }
        })
    
    serializer = CustomerRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        customer = serializer.save()
        response_data = {
            'customer_id': customer.customer_id,
            'name': customer.name,
            'age': customer.age,
            'monthly_income': customer.monthly_salary,
            'approved_limit': customer.approved_limit,
            'phone_number': customer.phone_number
        }
        return Response(response_data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def ingest_data(request):
    try:
        customer_df = pd.read_excel('customer_data.xlsx')
        customer_count = 0
        
        with transaction.atomic():
            for _, row in customer_df.iterrows():
                customer, created = Customer.objects.get_or_create(
                    customer_id=row['Customer ID'],
                    defaults={
                        'first_name': row['First Name'],
                        'last_name': row['Last Name'],
                        'age': row['Age'],
                        'phone_number': str(row['Phone Number']),
                        'monthly_salary': Decimal(str(row['Monthly Salary'])),
                        'approved_limit': Decimal(str(row['Approved Limit'])),
                        'current_debt': Decimal('0')
                    }
                )
                customer_count += 1
        
        loan_df = pd.read_excel('loan_data.xlsx')
        loan_count = 0
        
        with transaction.atomic():
            for _, row in loan_df.iterrows():
                try:
                    customer = Customer.objects.get(customer_id=row['Customer ID'])
                    
                    start_date = pd.to_datetime(row['Date of Approval']).date()
                    end_date = pd.to_datetime(row['End Date']).date()
                    
                    loan, created = Loan.objects.get_or_create(
                        loan_id=row['Loan ID'],
                        defaults={
                            'customer': customer,
                            'loan_amount': Decimal(str(row['Loan Amount'])),
                            'tenure': int(row['Tenure']),
                            'interest_rate': Decimal(str(row['Interest Rate'])),
                            'monthly_installment': Decimal(str(row['Monthly payment'])),
                            'emis_paid_on_time': int(row['EMIs paid on Time']),
                            'start_date': start_date,
                            'end_date': end_date,
                            'is_active': True
                        }
                    )
                    loan_count += 1
                        
                except Customer.DoesNotExist:
                    continue
        
        return Response({
            'message': f'Successfully ingested {customer_count} customers and {loan_count} loans',
            'customers_ingested': customer_count,
            'loans_ingested': loan_count
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'error': f'Error ingesting data: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET', 'POST'])
def check_loan_eligibility(request):
    if request.method == 'GET':
        return Response({
            'message': 'Loan Eligibility Check API',
            'method': 'POST',
            'fields': {
                'customer_id': 'integer',
                'loan_amount': 'decimal',
                'interest_rate': 'decimal',
                'tenure': 'integer'
            }
        })
    serializer = LoanEligibilitySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    customer_id = data['customer_id']
    loan_amount = data['loan_amount']
    interest_rate = data['interest_rate']
    tenure = data['tenure']
    
    try:
        customer = Customer.objects.get(customer_id=customer_id)
    except Customer.DoesNotExist:
        return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
    
    credit_score = calc_credit_score(customer)
    
    current_loans_sum = sum(loan.loan_amount for loan in customer.loans.filter(is_active=True))
    if current_loans_sum > customer.approved_limit:
        credit_score = 0
    
    current_emis_sum = sum(loan.monthly_installment for loan in customer.loans.filter(is_active=True))
    if current_emis_sum > (customer.monthly_salary * Decimal('0.5')):
        credit_score = 0
    
    approval, corrected_rate = check_approval(credit_score, interest_rate)
    monthly_emi = calc_emi(loan_amount, corrected_rate, tenure)
    
    response_data = {
        'customer_id': customer_id,
        'approval': approval,
        'interest_rate': interest_rate,
        'corrected_interest_rate': corrected_rate,
        'tenure': tenure,
        'monthly_installment': monthly_emi
    }
    
    return Response(response_data, status=status.HTTP_200_OK)


@api_view(['GET', 'POST'])
def create_loan(request):
    if request.method == 'GET':
        return Response({
            'message': 'Create Loan API',
            'method': 'POST',
            'fields': {
                'customer_id': 'integer',
                'loan_amount': 'decimal',
                'interest_rate': 'decimal',
                'tenure': 'integer'
            }
        })
    serializer = LoanCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    customer_id = data['customer_id']
    loan_amount = data['loan_amount']
    interest_rate = data['interest_rate']
    tenure = data['tenure']
    
    try:
        customer = Customer.objects.get(customer_id=customer_id)
    except Customer.DoesNotExist:
        return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
    
    credit_score = calc_credit_score(customer)
    
    current_loans_sum = sum(loan.loan_amount for loan in customer.loans.filter(is_active=True))
    if current_loans_sum > customer.approved_limit:
        credit_score = 0
    
    current_emis_sum = sum(loan.monthly_installment for loan in customer.loans.filter(is_active=True))
    if current_emis_sum > (customer.monthly_salary * Decimal('0.5')):
        credit_score = 0
    
    approval, corrected_rate = check_approval(credit_score, interest_rate)
    
    if not approval:
        response_data = {
            'loan_id': None,
            'customer_id': customer_id,
            'loan_approved': False,
            'message': 'Loan not approved due to low credit score or high debt',
            'monthly_installment': 0
        }
        return Response(response_data, status=status.HTTP_200_OK)
    
    monthly_emi = calc_emi(loan_amount, corrected_rate, tenure)
    
    with transaction.atomic():
        loan = Loan.objects.create(
            customer=customer,
            loan_amount=loan_amount,
            tenure=tenure,
            interest_rate=corrected_rate,
            monthly_installment=monthly_emi,
            start_date=date.today(),
            end_date=calc_end_date(tenure),
            is_active=True
        )
    
    response_data = {
        'loan_id': loan.loan_id,
        'customer_id': customer_id,
        'loan_approved': True,
        'message': 'Loan approved successfully',
        'monthly_installment': monthly_emi
    }
    
    return Response(response_data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
def view_loan(request, loan_id):
    try:
        loan = Loan.objects.get(loan_id=loan_id)
    except Loan.DoesNotExist:
        return Response({'error': 'Loan not found'}, status=status.HTTP_404_NOT_FOUND)
    
    serializer = LoanDetailSerializer(loan)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
def view_customer_loans(request, customer_id):
    try:
        customer = Customer.objects.get(customer_id=customer_id)
    except Customer.DoesNotExist:
        return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
    
    loans = customer.loans.all()
    serializer = CustomerLoanListSerializer(loans, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


def calc_credit_score(customer):
    loans = customer.loans.all()
    
    if not loans.exists():
        return 50
    
    total_emis_paid = sum(loan.emis_paid_on_time for loan in loans)
    total_emis_expected = sum(loan.tenure for loan in loans)
    payment_score = (Decimal(str(total_emis_paid)) / Decimal(str(total_emis_expected)) * Decimal('100')) if total_emis_expected > 0 else Decimal('0')
    
    loan_count = loans.count()
    loan_count_score = min(loan_count * 10, 30)
    
    current_year = date.today().year
    current_year_loans = loans.filter(start_date__year=current_year).count()
    activity_score = min(current_year_loans * 15, 20)
    
    total_loan_volume = sum(loan.loan_amount for loan in loans)
    volume_score = min(total_loan_volume / Decimal('1000000') * Decimal('10'), Decimal('20'))
    
    credit_score = (payment_score * Decimal('0.4') + Decimal(str(loan_count_score)) * Decimal('0.2') + 
                   Decimal(str(activity_score)) * Decimal('0.2') + volume_score * Decimal('0.2'))
    
    return min(credit_score, Decimal('100'))


def check_approval(credit_score, requested_rate):
    if credit_score > 50:
        return True, requested_rate
    elif credit_score > 30:
        if requested_rate > 12:
            return True, requested_rate
        else:
            return True, Decimal('12.0')
    elif credit_score > 10:
        if requested_rate > 16:
            return True, requested_rate
        else:
            return True, Decimal('16.0')
    else:
        return False, requested_rate


def calc_emi(loan_amount, interest_rate, tenure):
    if interest_rate == 0:
        return loan_amount / tenure
    
    monthly_rate = interest_rate / (12 * 100)
    
    if monthly_rate == 0:
        return loan_amount / tenure
    
    numerator = loan_amount * monthly_rate * ((1 + monthly_rate) ** tenure)
    denominator = ((1 + monthly_rate) ** tenure) - 1
    
    if denominator == 0:
        return loan_amount / tenure
    
    return numerator / denominator


def calc_end_date(tenure_months):
    from dateutil.relativedelta import relativedelta
    return date.today() + relativedelta(months=tenure_months)


urlpatterns = [
    path('', api_root, name='api_root'),
    path('register/', register_customer, name='register_customer'),
    path('ingest-data/', ingest_data, name='ingest_data'),
    path('check-eligibility/', check_loan_eligibility, name='check_loan_eligibility'),
    path('create-loan/', create_loan, name='create_loan'),
    path('view-loan/<int:loan_id>/', view_loan, name='view_loan'),
    path('view-loans/<int:customer_id>/', view_customer_loans, name='view_customer_loans'),
]
