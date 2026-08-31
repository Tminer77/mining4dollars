#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "AureliaVehiclePawn.generated.h"

class UBoxComponent;
class UCameraComponent;
class USpringArmComponent;
class UStaticMeshComponent;

/**
 * Arcade street-racer pawn that compiles against stock Engine meshes.
 * Drop a Chaos wheeled skeletal mesh on this class later for sim handling.
 * Graphics fidelity comes from Lumen / Nanite / City Sample — not from GTA 6 files.
 */
UCLASS()
class AURELIADRIVE_API AAureliaVehiclePawn : public APawn
{
	GENERATED_BODY()

public:
	AAureliaVehiclePawn();

	virtual void Tick(float DeltaSeconds) override;
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

	void SetThrottle(float Value);
	void SetSteer(float Value);
	void SetBrake(float Value);
	void HandbrakePressed();
	void HandbrakeReleased();
	void ResetToSpawn();

	UFUNCTION(BlueprintPure, Category = "Aurelia")
	float GetSpeedKmh() const;

protected:
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Aurelia")
	TObjectPtr<UBoxComponent> Collision;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Aurelia")
	TObjectPtr<UStaticMeshComponent> Body;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Aurelia")
	TObjectPtr<USpringArmComponent> SpringArm;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Aurelia")
	TObjectPtr<UCameraComponent> ChaseCamera;

	UPROPERTY(EditAnywhere, Category = "Aurelia|Handling")
	float MaxSpeedCms = 5500.f;

	UPROPERTY(EditAnywhere, Category = "Aurelia|Handling")
	float Acceleration = 2800.f;

	UPROPERTY(EditAnywhere, Category = "Aurelia|Handling")
	float BrakeDecel = 4200.f;

	UPROPERTY(EditAnywhere, Category = "Aurelia|Handling")
	float SteerRate = 70.f;

	UPROPERTY(EditAnywhere, Category = "Aurelia|Handling")
	float Drag = 0.35f;

private:
	float ThrottleInput = 0.f;
	float SteerInput = 0.f;
	float BrakeInput = 0.f;
	bool bHandbrake = false;
	float SpeedCms = 0.f;
	FVector SpawnLocation = FVector::ZeroVector;
	FRotator SpawnRotation = FRotator::ZeroRotator;
};
